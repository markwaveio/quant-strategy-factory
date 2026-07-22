"""决策器。

把「策略提案 + AI 评审 + 回测绩效」收敛成每个标的的一条决策:
- 只有综合评审 APPROVE 的策略才会进入决策(REVISE/REJECT 的转为 HOLD/AVOID 并记录原因)。
- 同一标的多个通过的策略,取综合评分最高者。
- 仓位建议 = 基础仓位 * 信心,并受回撤惩罚。
"""

from __future__ import annotations

from qsf.core.config import EvaluationConfig
from qsf.core.schema import (
    Action,
    BacktestMetrics,
    Decision,
    DecisionReport,
    EvaluationResult,
    Insight,
    MarketSnapshot,
    StrategyProposal,
    Verdict,
)


def _action_from_position(final_position: float) -> Action:
    """由策略「此刻的目标仓位」推导建议动作。

    **不能按策略族查表。** 均值回归在超涨时持仓 -1.0(做空),
    若一律映射成 BUY,一个靠做空赚钱的策略会被报成"买入"——
    报告层的严重误导。趋势族同理可能翻空。

    约定:>0 做多 / <0 做空 / ==0 当前无仓位(观望)。
    """
    if final_position > 0:
        return Action.BUY
    if final_position < 0:
        return Action.SELL
    return Action.HOLD


class Decider:
    def __init__(self, config: EvaluationConfig, *, base_weight: float = 0.2) -> None:
        self.config = config
        self.base_weight = base_weight

    def decide(
        self,
        snapshot: MarketSnapshot,
        insights: list[Insight],
        proposals: list[StrategyProposal],
        evaluations: list[EvaluationResult],
        backtests: dict[str, BacktestMetrics],
    ) -> DecisionReport:
        proposal_by_id = {p.id: p for p in proposals}

        # 按标的聚合通过的策略,取评分最高者
        best_by_symbol: dict[str, tuple[StrategyProposal, EvaluationResult]] = {}
        rejected_symbols: dict[str, str] = {}

        for ev in evaluations:
            prop = proposal_by_id.get(ev.strategy_id)
            if prop is None:
                continue
            if ev.final_verdict == Verdict.APPROVE:
                cur = best_by_symbol.get(prop.symbol)
                if cur is None or ev.aggregate_score > cur[1].aggregate_score:
                    best_by_symbol[prop.symbol] = (prop, ev)
            else:
                # 记录一个否决原因(仅当该标的还没有通过的策略时展示)
                reason = ev.risk_flags[0] if ev.risk_flags else "未通过综合评审"
                rejected_symbols.setdefault(prop.symbol, reason)

        decisions: list[Decision] = []
        for symbol in snapshot.symbols:
            if symbol in best_by_symbol:
                prop, ev = best_by_symbol[symbol]
                decisions.append(
                    self._make_decision(prop, ev, backtests.get(prop.id))
                )
            else:
                # 没有任何通过的策略 → 回避或持有
                reason = rejected_symbols.get(symbol, "无有效信号")
                decisions.append(
                    Decision(
                        symbol=symbol,
                        action=Action.AVOID,
                        strategy_id="-",
                        conviction=0.3,
                        suggested_weight=0.0,
                        reasons=[],
                        warnings=[reason],
                    )
                )

        # 决策按信心从高到低排
        decisions.sort(key=lambda d: d.conviction, reverse=True)

        return DecisionReport(
            as_of=snapshot.as_of,
            decisions=decisions,
            market_summary=self._market_summary(snapshot, insights, decisions),
            top_insights=[i.summary for i in insights[:5]],
        )

    def _make_decision(
        self,
        proposal: StrategyProposal,
        evaluation: EvaluationResult,
        metrics: BacktestMetrics | None,
    ) -> Decision:
        conviction = evaluation.aggregate_score
        # 按策略「此刻的实际仓位方向」定动作,而非按族查表
        final_pos = metrics.final_position if metrics is not None else 0.0
        action = _action_from_position(final_pos)

        # 仓位:基础 * 信心,再按回撤惩罚(回撤越大仓位越小)
        weight = self.base_weight * conviction
        warnings: list[str] = list(evaluation.risk_flags)
        if metrics is not None:
            dd = abs(metrics.max_drawdown)
            penalty = max(0.3, 1.0 - dd / max(self.config.max_drawdown_limit, 1e-6))
            weight *= penalty

        # 当前无仓位 → 策略此刻不建议入场,权重归零避免误导
        if action == Action.HOLD:
            weight = 0.0
            warnings.append("策略当前无持仓信号,建议观望而非入场")

        reasons = [proposal.rationale]
        if action == Action.SELL:
            reasons.append(f"策略当前为空头仓位({final_pos:+.1f}),建议方向为做空/减持")
        if metrics is not None:
            reasons.append(
                f"回测:夏普 {metrics.sharpe:.2f}、收益 {metrics.total_return:.1%}、回撤 {metrics.max_drawdown:.1%}"
            )

        return Decision(
            symbol=proposal.symbol,
            action=action,
            strategy_id=proposal.id,
            conviction=round(conviction, 3),
            suggested_weight=round(min(1.0, max(0.0, weight)), 3),
            reasons=reasons,
            warnings=warnings,
        )

    @staticmethod
    def _market_summary(snapshot, insights, decisions) -> str:
        n_buy = sum(1 for d in decisions if d.action == Action.BUY)
        n_avoid = sum(1 for d in decisions if d.action == Action.AVOID)
        n_events = sum(1 for i in insights if i.kind.value == "event")
        return (
            f"覆盖 {len(snapshot.symbols)} 个标的:建议做多 {n_buy}、回避 {n_avoid};"
            f"共 {len(insights)} 条洞察,其中事件类 {n_events} 条。"
        )
