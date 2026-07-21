"""基于 JSONL 的经验记忆库。

一条记录 = 某策略在某市场条件下的成败 + 一句教训。
成败判定标准(可调):
- 综合评审 APPROVE 且回测夏普>0、总收益>0     → SUCCESS
- 综合评审 REJECT,或总收益为负                → FAILURE
- 其余                                        → NEUTRAL
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from qsf.core.schema import (
    BacktestMetrics,
    EvaluationResult,
    MarketSnapshot,
    MemoryRecord,
    Outcome,
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
