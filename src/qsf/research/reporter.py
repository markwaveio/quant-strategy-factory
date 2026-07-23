"""研究证据报告。

本模块的职责是把一轮流水线里的候选策略、回测、评审和最终建议
整理成结构化证据包。它服务于"发现策略并送去模拟验证"的研究流程,
不表达任何实盘交易授权。
"""

from __future__ import annotations

import json

from qsf.core.schema import (
    Action,
    BacktestMetrics,
    DecisionReport,
    EvaluationResult,
    MarketSnapshot,
    ResearchReport,
    ResearchStatus,
    StrategyEvidenceCard,
    StrategyProposal,
    Verdict,
)


class ResearchReporter:
    """把流水线产物整理为策略研究证据卡。"""

    def build(
        self,
        *,
        snapshot: MarketSnapshot,
        proposals: list[StrategyProposal],
        evaluations: list[EvaluationResult],
        backtests: dict[str, BacktestMetrics],
        decision_report: DecisionReport,
    ) -> ResearchReport:
        ev_by_id = {e.strategy_id: e for e in evaluations}
        action_by_strategy = {
            d.strategy_id: d.action
            for d in decision_report.decisions
            if d.strategy_id != "-"
        }

        cards: list[StrategyEvidenceCard] = []
        for proposal in proposals:
            ev = ev_by_id.get(proposal.id)
            if ev is None:
                continue
            metrics = backtests.get(proposal.id)
            action = action_by_strategy.get(proposal.id, Action.AVOID)
            status = _status(ev, action)
            cards.append(
                StrategyEvidenceCard(
                    strategy_id=proposal.id,
                    symbol=proposal.symbol,
                    family=proposal.family,
                    status=status,
                    action=action,
                    params=dict(proposal.params),
                    hypothesis=proposal.rationale,
                    triggered_by=list(proposal.triggered_by),
                    final_verdict=ev.final_verdict,
                    aggregate_score=ev.aggregate_score,
                    metrics=metrics,
                    reasons=_reasons(proposal, metrics),
                    warnings=_warnings(ev, metrics),
                    next_step=_next_step(status, ev, metrics),
                )
            )

        return ResearchReport(
            as_of=snapshot.as_of,
            summary=_summary(cards),
            cards=cards,
        )


def render_research_markdown(report: ResearchReport) -> str:
    """把研究证据包渲染成 Markdown,用于归档或人工复核。"""
    lines: list[str] = []
    lines.append(f"# 策略研究证据包 · {report.as_of:%Y-%m-%d %H:%M}")
    lines.append("")
    lines.append(f"> {report.summary}")
    lines.append("")
    lines.append("## 候选策略")
    lines.append("")
    lines.append("| 状态 | 标的 | 策略族 | 动作 | 评分 | 收益 | 超额 | OOS超额 | 交易 | 参数 |")
    lines.append("| --- | --- | --- | --- | ---: | ---: | ---: | ---: | ---: | --- |")
    for card in report.cards:
        m = card.metrics
        total = _pct(m.total_return) if m else "-"
        excess = _maybe_pct(m.excess_return) if m else "-"
        oos = _maybe_pct(m.oos_excess) if m else "-"
        trades = str(m.n_trades) if m else "-"
        params = ", ".join(f"{k}={v:g}" for k, v in card.params.items()) or "-"
        lines.append(
            f"| {card.status.value} | {card.symbol} | {card.family.value} | "
            f"{card.action.value} | {card.aggregate_score:.3f} | {total} | "
            f"{excess} | {oos} | {trades} | {params} |"
        )
    lines.append("")

    for card in report.cards:
        lines.append(f"### {card.strategy_id}")
        lines.append(f"- 假设:{card.hypothesis}")
        lines.append(f"- 下一步:{card.next_step}")
        for warning in card.warnings[:5]:
            lines.append(f"- 风险:{warning}")
        lines.append("")

    lines.append(f"---\n\n_{report.disclaimer}_")
    return "\n".join(lines)


def strategy_payload(report: ResearchReport) -> dict:
    """导出给模拟交易/外部回测系统的紧凑策略 payload。

    不包含 `equity_curve` 这类研究展示字段,避免 JSON 过大。模拟系统需要的是:
    标的、策略族、参数、方向、状态、核心指标和风险说明。
    """
    return {
        "as_of": report.as_of.isoformat(),
        "summary": report.summary,
        "strategies": [_strategy_card_payload(card) for card in report.cards],
        "disclaimer": report.disclaimer,
    }


def strategy_payload_json(report: ResearchReport) -> str:
    return json.dumps(strategy_payload(report), ensure_ascii=False, indent=2)


def _status(ev: EvaluationResult, action: Action) -> ResearchStatus:
    if ev.final_verdict == Verdict.APPROVE and action in (Action.BUY, Action.SELL):
        return ResearchStatus.PROMOTE_TO_SIM
    if ev.final_verdict == Verdict.REJECT:
        return ResearchStatus.REJECT
    return ResearchStatus.WATCHLIST


def _reasons(proposal: StrategyProposal, metrics: BacktestMetrics | None) -> list[str]:
    reasons = [proposal.rationale]
    if metrics is not None:
        reasons.append(
            f"回测收益 {metrics.total_return:.1%},夏普 {metrics.sharpe:.2f},"
            f"最大回撤 {metrics.max_drawdown:.1%}"
        )
        if metrics.excess_return is not None:
            reasons.append(f"相对买入持有超额 {metrics.excess_return:+.1%}")
        if metrics.oos_excess is not None:
            reasons.append(f"样本外超额 {metrics.oos_excess:+.1%}")
    return reasons


def _warnings(ev: EvaluationResult, metrics: BacktestMetrics | None) -> list[str]:
    warnings = list(ev.risk_flags)
    if metrics is None:
        return warnings
    if metrics.n_trades < 10:
        warnings.append(f"交易样本仅 {metrics.n_trades} 笔,统计显著性不足")
    if metrics.excess_return is not None and metrics.excess_return <= 0:
        warnings.append("未跑赢买入持有基准")
    if metrics.oos_excess is not None and metrics.oos_excess <= 0:
        warnings.append("样本外未跑赢买入持有基准")
    return list(dict.fromkeys(warnings))


def _next_step(
    status: ResearchStatus,
    ev: EvaluationResult,
    metrics: BacktestMetrics | None,
) -> str:
    if status == ResearchStatus.PROMOTE_TO_SIM:
        return "进入模拟交易或外部回测验证,记录真实滑点、成交与持仓偏差"
    if status == ResearchStatus.WATCHLIST:
        if metrics is not None and metrics.n_trades < 10:
            return "延长样本或扩大标的池,补足交易次数后复验"
        return "补充样本外、参数高原和跨标的稳健性证据"
    if ev.risk_flags:
        return f"本轮不进入下一步:{ev.risk_flags[0]}"
    return "本轮不进入下一步:未通过研究评审"


def _summary(cards: list[StrategyEvidenceCard]) -> str:
    n_sim = sum(1 for c in cards if c.status == ResearchStatus.PROMOTE_TO_SIM)
    n_watch = sum(1 for c in cards if c.status == ResearchStatus.WATCHLIST)
    n_reject = sum(1 for c in cards if c.status == ResearchStatus.REJECT)
    return (
        f"候选 {len(cards)} 个,进入模拟验证 {n_sim} 个,"
        f"观察 {n_watch} 个,否决 {n_reject} 个。"
    )


def _strategy_card_payload(card: StrategyEvidenceCard) -> dict:
    m = card.metrics
    metrics = None
    if m is not None:
        metrics = {
            "total_return": m.total_return,
            "annual_return": m.annual_return,
            "max_drawdown": m.max_drawdown,
            "sharpe": m.sharpe,
            "win_rate": m.win_rate,
            "n_trades": m.n_trades,
            "benchmark_return": m.benchmark_return,
            "excess_return": m.excess_return,
            "oos_return": m.oos_return,
            "oos_benchmark_return": m.oos_benchmark_return,
            "oos_excess": m.oos_excess,
            "final_position": m.final_position,
        }
    return {
        "strategy_id": card.strategy_id,
        "symbol": card.symbol,
        "family": card.family.value,
        "status": card.status.value,
        "action": card.action.value,
        "params": card.params,
        "hypothesis": card.hypothesis,
        "triggered_by": card.triggered_by,
        "final_verdict": card.final_verdict.value,
        "aggregate_score": card.aggregate_score,
        "metrics": metrics,
        "warnings": card.warnings,
        "next_step": card.next_step,
    }


def _pct(value: float) -> str:
    return f"{value:.1%}"


def _maybe_pct(value: float | None) -> str:
    return "-" if value is None else f"{value:+.1%}"
