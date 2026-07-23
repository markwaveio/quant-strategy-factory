"""评估委员会:编排三个 AI 角色,汇总成最终结论。

聚合规则(投委会式,风险官有一票否决):
- 风险官 REJECT → 直接否决(最坏亏损不可接受,红线优先)。
- 任一角色 REJECT 且综合官也不看好 → 否决。
- 全部 APPROVE → 采纳。
- 其余 → 修改后再议(REVISE)。

综合评分 = 各角色 verdict 得分按 confidence 加权(approve=1, revise=0.5, reject=0)。
"""

from __future__ import annotations

from qsf.core.config import EvaluationConfig
from qsf.core.schema import (
    BacktestMetrics,
    EvaluationResult,
    MarketSnapshot,
    MemoryRecord,
    ReviewOpinion,
    ReviewRole,
    StrategyProposal,
    Verdict,
)
from qsf.evaluation.judge import LLMJudge, MockLLMJudge, ReviewContext

_VERDICT_SCORE = {Verdict.APPROVE: 1.0, Verdict.REVISE: 0.5, Verdict.REJECT: 0.0}


class EvaluationBoard:
    def __init__(
        self, config: EvaluationConfig, judge: LLMJudge | None = None
    ) -> None:
        self.config = config
        self.judge = judge or self._build_judge(config)
        self.roles = [ReviewRole(r) for r in config.roles]

    @staticmethod
    def _build_judge(config: EvaluationConfig) -> LLMJudge:
        if config.provider == "anthropic":
            # 延迟到真正需要时再实例化,避免 mock 模式强依赖
            from qsf.evaluation.judge import ClaudeLLMJudge

            return ClaudeLLMJudge(model=config.model)
        return MockLLMJudge()

    def evaluate(
        self,
        proposal: StrategyProposal,
        metrics: BacktestMetrics,
        snapshot: MarketSnapshot,
        past_memory: list[MemoryRecord],
    ) -> EvaluationResult:
        ctx = self._build_context(proposal, metrics, snapshot, past_memory)
        opinions = [self.judge.review(role, ctx) for role in self.roles]
        final = self._aggregate(opinions)
        score = self._score(opinions)
        risk_flags = self._risk_flags(opinions)
        return EvaluationResult(
            strategy_id=proposal.id,
            opinions=opinions,
            final_verdict=final,
            risk_flags=risk_flags,
            aggregate_score=round(score, 3),
        )

    def _build_context(
        self, proposal, metrics, snapshot, past_memory
    ) -> ReviewContext:
        feat = snapshot.features.get(proposal.symbol)
        market_features: dict[str, float] = {}
        if feat is not None:
            market_features = {
                "volatility": feat.volatility or 0.0,
                "momentum": feat.momentum or 0.0,
                "trend_strength": feat.trend_strength or 0.0,
                "sentiment": feat.sentiment or 0.0,
                "black_swan_score": feat.black_swan_score,
            }
        similar_failures = sum(
            1
            for r in past_memory
            if r.family == proposal.family
            and r.symbol == proposal.symbol
            and r.outcome.value == "failure"
        )
        return ReviewContext(
            proposal=proposal,
            metrics=metrics,
            market_features=market_features,
            similar_failures=similar_failures,
            max_drawdown_limit=self.config.max_drawdown_limit,
            data_quality=snapshot.quality,
        )

    def _aggregate(self, opinions: list[ReviewOpinion]) -> Verdict:
        by_role = {op.role: op for op in opinions}
        # 风险官一票否决
        risk = by_role.get(ReviewRole.RISK_OFFICER)
        if risk and risk.verdict == Verdict.REJECT:
            return Verdict.REJECT

        verdicts = [op.verdict for op in opinions]
        if all(v == Verdict.APPROVE for v in verdicts):
            return Verdict.APPROVE
        if Verdict.REJECT in verdicts:
            # 有人否决:看综合官态度,不看好则否决
            committee = by_role.get(ReviewRole.COMMITTEE)
            if committee and committee.verdict == Verdict.REJECT:
                return Verdict.REJECT
            return Verdict.REVISE
        return Verdict.REVISE

    def _score(self, opinions: list[ReviewOpinion]) -> float:
        if not opinions:
            return 0.0
        num = sum(_VERDICT_SCORE[op.verdict] * op.confidence for op in opinions)
        den = sum(op.confidence for op in opinions) or 1.0
        return num / den

    def _risk_flags(self, opinions: list[ReviewOpinion]) -> list[str]:
        flags: list[str] = []
        for op in opinions:
            flags.extend(op.concerns)
        return flags
