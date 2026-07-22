"""基于 JSONL 的经验记忆库。

一条记录 = 某策略在某市场条件下的成败 + 一句教训。
成败判定标准(可调):
- 综合评审 APPROVE 且回测夏普>0、总收益>0     → SUCCESS
- 综合评审 REJECT,或总收益为负                → FAILURE
- 其余                                        → NEUTRAL
"""

from __future__ import annotations

import math
from datetime import datetime
from pathlib import Path

from qsf.core.schema import (
    BacktestMetrics,
    BacktestProvenance,
    EvaluationResult,
    MarketSnapshot,
    MemoryRecord,
    Outcome,
    ParamSearchInfo,
    StrategyProposal,
    Verdict,
)


class MemoryStore:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    # 写入 --------------------------------------------------------------- #

    def remember(
        self,
        proposal: StrategyProposal,
        metrics: BacktestMetrics,
        snapshot: MarketSnapshot,
        evaluation: EvaluationResult,
        *,
        provenance: BacktestProvenance | None = None,
        param_search: ParamSearchInfo | None = None,
        regime: str = "",
    ) -> MemoryRecord:
        outcome = self._judge_outcome(metrics, evaluation)
        feat = snapshot.features.get(proposal.symbol)
        context: dict[str, float] = {}
        if feat is not None:
            context = {
                "volatility": feat.volatility or 0.0,
                "trend_strength": feat.trend_strength or 0.0,
                "black_swan_score": feat.black_swan_score,
            }
        record = MemoryRecord(
            strategy_id=proposal.id,
            family=proposal.family,
            symbol=proposal.symbol,
            created_at=datetime.now(),
            outcome=outcome,
            metrics=metrics,
            market_context=context,
            lesson=self._lesson(proposal, metrics, evaluation, outcome),
            params=dict(proposal.params),
            provenance=provenance,
            param_search=param_search,
            regime=regime,
        )
        with self.path.open("a", encoding="utf-8") as f:
            f.write(record.model_dump_json() + "\n")
        return record

    # 读取 --------------------------------------------------------------- #

    def all(self) -> list[MemoryRecord]:
        if not self.path.exists():
            return []
        records: list[MemoryRecord] = []
        with self.path.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                records.append(MemoryRecord.model_validate_json(line))
        return records

    def query(
        self,
        *,
        family=None,
        symbol: str | None = None,
        outcome: Outcome | None = None,
    ) -> list[MemoryRecord]:
        """按族/标的/成败过滤,供评估层与决策层检索历史经验。"""
        out = self.all()
        if family is not None:
            out = [r for r in out if r.family == family]
        if symbol is not None:
            out = [r for r in out if r.symbol == symbol]
        if outcome is not None:
            out = [r for r in out if r.outcome == outcome]
        return out

    def stats(self) -> dict[str, int]:
        counts = {o.value: 0 for o in Outcome}
        for r in self.all():
            counts[r.outcome.value] += 1
        return counts

    # 相似市况检索 ------------------------------------------------------- #

    def find_similar(
        self,
        context: dict[str, float],
        *,
        family=None,
        top_k: int = 5,
        max_distance: float = 1.0,
    ) -> list[tuple[MemoryRecord, float]]:
        """找出「当时市场状况与现在相似」的历史记录,按相似度排序。

        这是记忆层真正有用的地方 —— 不是"这个策略以前亏过",
        而是"**在类似的市场环境下**,这个策略族表现如何"。

        距离用归一化欧氏:每个特征按其量纲缩放后求差。
        返回 [(记录, 距离)],距离越小越相似;超过 max_distance 的丢弃。
        """
        # 各特征的典型量级,用于归一化(避免波动率 0.3 和 z-score 2.0 直接相减)
        scale = {
            "volatility": 0.3,
            "trend_strength": 0.5,
            "momentum": 0.15,
            "black_swan_score": 1.0,
            "vol_ratio": 1.0,
        }
        out: list[tuple[MemoryRecord, float]] = []
        for r in self.all():
            if family is not None and r.family != family:
                continue
            keys = set(context) & set(r.market_context)
            if not keys:
                continue
            dist = math.sqrt(
                sum(
                    ((context[k] - r.market_context[k]) / scale.get(k, 1.0)) ** 2
                    for k in keys
                )
                / len(keys)
            )
            if dist <= max_distance:
                out.append((r, round(dist, 4)))
        out.sort(key=lambda t: t[1])
        return out[:top_k]

    def searched_spaces(self, family=None, symbol: str | None = None) -> list[dict]:
        """列出已经搜索过的参数空间 —— 避免重复搜同一片区域。

        「已搜过 fast5~20 × slow20~60 全部无效」比没有记录有价值得多。
        """
        out = []
        for r in self.query(family=family, symbol=symbol):
            ps = r.param_search
            if ps and ps.searched and ps.space:
                out.append({
                    "strategy_id": r.strategy_id, "space": ps.space,
                    "n_evaluated": ps.n_evaluated, "outcome": r.outcome.value,
                    "lesson": r.lesson,
                })
        return out

    # 内部逻辑 ----------------------------------------------------------- #

    @staticmethod
    def _judge_outcome(
        metrics: BacktestMetrics, evaluation: EvaluationResult
    ) -> Outcome:
        if evaluation.final_verdict == Verdict.REJECT or metrics.total_return < 0:
            return Outcome.FAILURE
        if (
            evaluation.final_verdict == Verdict.APPROVE
            and metrics.sharpe > 0
            and metrics.total_return > 0
        ):
            return Outcome.SUCCESS
        return Outcome.NEUTRAL

    @staticmethod
    def _lesson(
        proposal: StrategyProposal,
        metrics: BacktestMetrics,
        evaluation: EvaluationResult,
        outcome: Outcome,
    ) -> str:
        fam = proposal.family.value
        if outcome == Outcome.SUCCESS:
            return f"{fam} 在 {proposal.symbol} 有效:夏普 {metrics.sharpe:.2f}、收益 {metrics.total_return:.1%}"
        if outcome == Outcome.FAILURE:
            reason = evaluation.risk_flags[0] if evaluation.risk_flags else "收益不佳"
            return f"{fam} 在 {proposal.symbol} 失败:{reason}"
        return f"{fam} 在 {proposal.symbol} 表现中性,需更多样本"
