"""复盘器:总结一轮运行的表现,提炼收获与改进点。"""

from __future__ import annotations

from datetime import date

from qsf.core.schema import (
    BacktestMetrics,
    EvaluationResult,
    ReviewNote,
    StrategyProposal,
    Verdict,
)


class Reviewer:
    def review(
        self,
        *,
        run_id: str,
        run_date: date,
        proposals: list[StrategyProposal],
        evaluations: list[EvaluationResult],
        backtests: dict[str, BacktestMetrics],
    ) -> ReviewNote:
        n_approved = sum(
            1 for e in evaluations if e.final_verdict == Verdict.APPROVE
        )
        n_rejected = sum(
            1 for e in evaluations if e.final_verdict == Verdict.REJECT
        )

        # 找最佳策略(按夏普)
        best_id: str | None = None
        best_sharpe: float | None = None
        for sid, m in backtests.items():
            if best_sharpe is None or m.sharpe > best_sharpe:
                best_sharpe, best_id = m.sharpe, sid

        return ReviewNote(
            run_id=run_id,
            run_date=run_date,
            n_proposals=len(proposals),
            n_approved=n_approved,
            n_rejected=n_rejected,
            best_strategy_id=best_id,
            best_sharpe=round(best_sharpe, 3) if best_sharpe is not None else None,
            key_takeaways=self._takeaways(evaluations, n_approved, n_rejected),
            improvements=self._improvements(evaluations, backtests),
        )

    @staticmethod
    def _takeaways(evaluations, n_approved, n_rejected) -> list[str]:
        out: list[str] = []
        total = len(evaluations)
        if total:
            out.append(
                f"本轮 {total} 个策略,{n_approved} 个通过、{n_rejected} 个否决,"
                f"通过率 {n_approved / total:.0%}"
            )
        # 收集出现最多的风险点
        flag_counts: dict[str, int] = {}
        for e in evaluations:
            for f in e.risk_flags:
                # 只取风险点的类型前缀,避免过于零散
                key = f.split(":")[0]
                flag_counts[key] = flag_counts.get(key, 0) + 1
        if flag_counts:
            top = max(flag_counts.items(), key=lambda kv: kv[1])
            out.append(f"最常见的风险点:{top[0]}(出现 {top[1]} 次)")
        return out

    @staticmethod
    def _improvements(evaluations, backtests) -> list[str]:
        out: list[str] = []
        # 如果否决很多,提示放宽或调参
        if evaluations:
            reject_rate = sum(
                1 for e in evaluations if e.final_verdict == Verdict.REJECT
            ) / len(evaluations)
            if reject_rate > 0.5:
                out.append("否决率偏高:考虑收紧策略生成条件,减少低质量提案")
        # 如果普遍交易过少,提示延长回测
        if backtests:
            avg_trades = sum(m.n_trades for m in backtests.values()) / len(backtests)
            if avg_trades < 5:
                out.append("平均交易笔数偏少:延长回测区间以提升统计显著性")
        if not out:
            out.append("本轮表现平稳,维持当前参数并持续积累记忆")
        return out
