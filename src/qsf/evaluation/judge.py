"""LLM 评审抽象接口 + 两个实现。

设计要点:
- `LLMJudge.review(role, context)` 是唯一契约:给定角色和一份「评审上下文」,
  返回该角色的结构化意见(verdict / concerns / suggestions)。
- `MockLLMJudge`:纯规则实现,不需要网络和 key,保证流水线离线可跑、可复现。
  它用回测指标和市场特征做确定性判断,足以驱动整条链路。
- `ClaudeLLMJudge`:真实实现的骨架。默认不启用(provider=mock);
  填好 ANTHROPIC_API_KEY 且把 config.evaluation.provider 设为 "anthropic" 后启用。
  用最新的 Claude Opus 4.8 模型 + 自适应思考,结构化输出用 output_config.format 约束。
"""

from __future__ import annotations

import json
from abc import ABC, abstractmethod
from dataclasses import dataclass

from qsf.core.schema import (
    BacktestMetrics,
    ReviewOpinion,
    ReviewRole,
    StrategyProposal,
    Verdict,
)


@dataclass
class ReviewContext:
    """喂给评审员的一份完整上下文(角色无关,便于复用)。"""

    proposal: StrategyProposal
    metrics: BacktestMetrics
    market_features: dict[str, float]       # 该标的的关键特征快照
    similar_failures: int                   # 记忆层里同族同标的失败过多少次
    max_drawdown_limit: float               # 风险红线(来自配置)

    def to_prompt_dict(self) -> dict:
        """整理成可读、可序列化的结构,给真实 LLM 用。"""
        return {
            "strategy": {
                "id": self.proposal.id,
                "family": self.proposal.family.value,
                "symbol": self.proposal.symbol,
                "rationale": self.proposal.rationale,
                "params": self.proposal.params,
                "triggered_by": self.proposal.triggered_by,
            },
            "backtest": {
                "total_return": self.metrics.total_return,
                "annual_return": self.metrics.annual_return,
                "max_drawdown": self.metrics.max_drawdown,
                "sharpe": self.metrics.sharpe,
                "win_rate": self.metrics.win_rate,
                "n_trades": self.metrics.n_trades,
            },
            "market_features": self.market_features,
            "memory": {"similar_failures": self.similar_failures},
            "risk_policy": {"max_drawdown_limit": self.max_drawdown_limit},
        }


class LLMJudge(ABC):
    """评审员接口。每个角色调用一次 review。"""

    @abstractmethod
    def review(self, role: ReviewRole, ctx: ReviewContext) -> ReviewOpinion: ...


# --------------------------------------------------------------------------- #
# Mock 实现:确定性规则,离线可跑                                                #
# --------------------------------------------------------------------------- #


class MockLLMJudge(LLMJudge):
    """用规则模拟三个角色的判断,保证无网络也能跑通全流程。

    规则是刻意「像那么回事」的:参数越极端、回撤越大、失败史越多,
    越倾向 REVISE/REJECT;夏普和胜率好则倾向 APPROVE。
    """

    def review(self, role: ReviewRole, ctx: ReviewContext) -> ReviewOpinion:
        if role == ReviewRole.SKEPTIC:
            return self._skeptic(ctx)
        if role == ReviewRole.RISK_OFFICER:
            return self._risk_officer(ctx)
        return self._committee(ctx)

    # 质疑者:盯过拟合与参数合理性
    def _skeptic(self, ctx: ReviewContext) -> ReviewOpinion:
        concerns: list[str] = []
        suggestions: list[str] = []
        m = ctx.metrics

        # 交易次数太少 → 统计不显著,可能过拟合
        if m.n_trades < 5:
            concerns.append(f"交易样本仅 {m.n_trades} 笔,统计不显著,疑似过拟合")
            suggestions.append("延长回测区间或放宽入场条件以获得更多样本")
        # 夏普高得不真实
        if m.sharpe > 3.0:
            concerns.append(f"夏普 {m.sharpe:.2f} 偏高,警惕曲线拟合")
        # 参数是不是明显拍脑袋(用极端值粗判)
        for k, v in ctx.proposal.params.items():
            if v != 0 and (abs(v) > 100 or (0 < abs(v) < 1e-3)):
                concerns.append(f"参数 {k}={v} 取值可疑,像是手工凑出来的")

        verdict = Verdict.APPROVE if not concerns else Verdict.REVISE
        confidence = 0.8 if not concerns else max(0.4, 0.8 - 0.1 * len(concerns))
        return ReviewOpinion(
            role=ReviewRole.SKEPTIC,
            strategy_id=ctx.proposal.id,
            verdict=verdict,
            confidence=round(confidence, 2),
            concerns=concerns,
            suggestions=suggestions,
            rationale="从过拟合与参数稳健性角度审查",
        )

    # 风险官:盯最坏亏损
    def _risk_officer(self, ctx: ReviewContext) -> ReviewOpinion:
        concerns: list[str] = []
        m = ctx.metrics
        dd = abs(m.max_drawdown)
        limit = ctx.max_drawdown_limit

        verdict = Verdict.APPROVE
        if dd > limit:
            concerns.append(
                f"最大回撤 {dd:.1%} 超过风险红线 {limit:.0%},最坏亏损不可接受"
            )
            verdict = Verdict.REJECT
        elif dd > limit * 0.8:
            concerns.append(f"最大回撤 {dd:.1%} 逼近红线 {limit:.0%},仓位需压缩")
            verdict = Verdict.REVISE

        # 黑天鹅暴露
        swan = ctx.market_features.get("black_swan_score", 0.0)
        if swan >= 0.3:
            concerns.append(f"标的存在黑天鹅暴露(强度 {swan:.2f}),尾部风险偏高")
            if verdict == Verdict.APPROVE:
                verdict = Verdict.REVISE

        confidence = 0.85 if verdict == Verdict.APPROVE else 0.7
        return ReviewOpinion(
            role=ReviewRole.RISK_OFFICER,
            strategy_id=ctx.proposal.id,
            verdict=verdict,
            confidence=confidence,
            concerns=concerns,
            suggestions=["设置止损", "按波动率缩放仓位"] if concerns else [],
            rationale=f"最坏亏损评估:回撤 {dd:.1%} vs 红线 {limit:.0%}",
        )

    # 综合评估官:像投委会一样裁决
    def _committee(self, ctx: ReviewContext) -> ReviewOpinion:
        concerns: list[str] = []
        m = ctx.metrics

        # 历史失败太多 → 谨慎
        if ctx.similar_failures >= 2:
            concerns.append(
                f"同族策略在该标的历史失败 {ctx.similar_failures} 次,记忆层警示"
            )
        # 收益与风险综合:夏普达标、正收益、回撤在红线内即可采纳
        # (0.5 是"可接受的风险调整后收益"下限;更高标准留给真实 LLM 评审去把关)
        good = m.sharpe >= 0.5 and m.total_return > 0 and abs(m.max_drawdown) < ctx.max_drawdown_limit
        if good and not concerns:
            verdict = Verdict.APPROVE
        elif m.total_return <= 0 or m.sharpe < 0:
            verdict = Verdict.REJECT
            concerns.append("风险调整后收益不佳(夏普/总收益为负)")
        else:
            verdict = Verdict.REVISE

        confidence = 0.75
        return ReviewOpinion(
            role=ReviewRole.COMMITTEE,
            strategy_id=ctx.proposal.id,
            verdict=verdict,
            confidence=confidence,
            concerns=concerns,
            suggestions=[],
            rationale="综合收益、风险、历史经验的投委会裁决",
        )


# --------------------------------------------------------------------------- #
# 真实实现骨架:Claude(默认不启用)                                            #
# --------------------------------------------------------------------------- #

# 每个角色的系统提示。真实评审时,这三段决定了 AI 的"人设"。
_ROLE_SYSTEM_PROMPTS: dict[ReviewRole, str] = {
    ReviewRole.SKEPTIC: (
        "你是量化策略的质疑者。你的职责是找茬:这个策略是不是过拟合?"
        "参数是不是拍脑袋?回测好看是不是曲线拟合的结果?"
        "只关注方法论的可靠性,给出严格但公允的意见。"
    ),
    ReviewRole.RISK_OFFICER: (
        "你是风险官。你只关心一个问题:最坏情况能亏多少,是否超出承受范围。"
        "重点审查最大回撤、尾部风险、黑天鹅暴露。宁可保守,不可冒进。"
    ),
    ReviewRole.COMMITTEE: (
        "你是投资委员会主席。综合质疑者与风险官的意见,以及历史经验,"
        "对策略做最终裁决:approve(采纳)、revise(修改后再议)、reject(否决)。"
    ),
}

# 约束 AI 返回严格 JSON,便于程序解析(对应 output_config.format 的 schema)。
_OPINION_SCHEMA = {
    "type": "object",
    "properties": {
        "verdict": {"type": "string", "enum": ["approve", "revise", "reject"]},
        "confidence": {"type": "number"},
        "concerns": {"type": "array", "items": {"type": "string"}},
        "suggestions": {"type": "array", "items": {"type": "string"}},
        "rationale": {"type": "string"},
    },
    "required": ["verdict", "confidence", "concerns", "suggestions", "rationale"],
    "additionalProperties": False,
}


class ClaudeLLMJudge(LLMJudge):
    """用 Claude 做真实评审(骨架)。

    默认不被流水线使用——只有把 config.evaluation.provider 设为 "anthropic"
    且环境变量里有 ANTHROPIC_API_KEY 时才会走到这里。

    这里刻意保持轻量:构造时才 import anthropic,避免 mock 模式下强依赖。
    真实调用用最新的 Claude Opus 4.8(claude-opus-4-8)+ 自适应思考,
    并用 output_config.format 把输出约束成 _OPINION_SCHEMA。
    """

    def __init__(self, model: str = "claude-opus-4-8", api_key: str | None = None) -> None:
        try:
            import anthropic  # 延迟导入:mock 模式无需安装
        except ImportError as e:  # pragma: no cover - 仅在真实启用时触发
            raise RuntimeError(
                "使用 ClaudeLLMJudge 需要安装 anthropic:pip install 'quant-strategy-factory[llm]'"
            ) from e
        self._client = anthropic.Anthropic(api_key=api_key) if api_key else anthropic.Anthropic()
        self._model = model

    def review(self, role: ReviewRole, ctx: ReviewContext) -> ReviewOpinion:  # pragma: no cover
        payload = ctx.to_prompt_dict()
        user_msg = (
            "请评审下面这个量化策略提案,只返回符合约定 schema 的 JSON。\n\n"
            + json.dumps(payload, ensure_ascii=False, indent=2)
        )
        resp = self._client.messages.create(
            model=self._model,
            max_tokens=1024,
            system=_ROLE_SYSTEM_PROMPTS[role],
            thinking={"type": "adaptive"},
            output_config={"format": {"type": "json_schema", "schema": _OPINION_SCHEMA}},
            messages=[{"role": "user", "content": user_msg}],
        )
        text = next((b.text for b in resp.content if b.type == "text"), "{}")
        data = json.loads(text)
        return ReviewOpinion(
            role=role,
            strategy_id=ctx.proposal.id,
            verdict=Verdict(data["verdict"]),
            confidence=float(data["confidence"]),
            concerns=list(data.get("concerns", [])),
            suggestions=list(data.get("suggestions", [])),
            rationale=data.get("rationale", ""),
        )
