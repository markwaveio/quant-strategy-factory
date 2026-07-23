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
import math
from abc import ABC, abstractmethod
from dataclasses import dataclass

from qsf.core.schema import (
    BacktestMetrics,
    DataQualityReport,
    FactorEvidence,
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
    # 证据链(可选)—— 质疑者先审证据、再审策略。
    # 补这两项是因为:2026-07 有个「净年化 78.7%」的因子完全是数据 bug 加
    # 选池前视偏差造出来的,而当时的质疑者只盯策略逻辑,一句都问不到点上。
    evidence: FactorEvidence | None = None
    data_quality: DataQualityReport | None = None

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
            # 证据链放在最前面审 —— 数据错了,策略逻辑再漂亮也没意义
            "evidence": (
                {
                    **self.evidence.model_dump(exclude={"quantiles"}),
                    "checks": self.evidence.passed(),
                    "t_threshold": round(self.evidence.bonferroni_threshold, 2),
                }
                if self.evidence else None
            ),
            "data_quality": (
                {
                    "n_symbols": self.data_quality.n_symbols,
                    "n_dropped": self.data_quality.n_dropped,
                    "index_alignment_safe": self.data_quality.index_alignment_safe,
                    "fatal_issues": [i.detail for i in self.data_quality.fatal],
                }
                if self.data_quality else None
            ),
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

    # 质疑者:先审证据,再审策略
    #
    # 顺序是刻意的。策略逻辑再漂亮,若底下的数据或方法是错的,审它没有意义。
    # 实测教训:一个「净年化 78.7%、夏普 1.31、单调、成本极不敏感」的因子,
    # 全部漂亮指标都来自两个数据 bug 加一层选池前视偏差。
    def _skeptic(self, ctx: ReviewContext) -> ReviewOpinion:
        concerns: list[str] = []
        suggestions: list[str] = []
        m = ctx.metrics

        # ── 第一关:数据本身可信吗 ──────────────────────────────
        dq = ctx.data_quality
        if dq is not None:
            for issue in dq.fatal:
                concerns.append(f"数据不可信:{issue.symbol} {issue.detail}")
            if not dq.index_alignment_safe:
                concerns.append(
                    f"各标的 bar 数量 {dq.bar_count_min}~{dq.bar_count_max},"
                    "若按数组下标做横截面,比较的根本不是同一天"
                )
                suggestions.append("横截面一律按日期对齐(Panel),禁用数组下标")
            if dq.n_dropped:
                concerns.append(
                    f"{dq.n_dropped} 只标的因数据问题被剔除,"
                    "剩余样本可能已不能代表原标的池"
                )

        # ── 第二关:证据链撑得住吗 ──────────────────────────────
        ev = ctx.evidence
        if ev is not None:
            checks = ev.passed()
            if not checks["多重检验"]:
                concerns.append(
                    f"t={ev.t_stat:.2f} 未过多重检验门槛 "
                    f"{ev.bonferroni_threshold:.2f}(共检验 {ev.n_hypotheses} 个假设)"
                    " —— 这个显著性可能只是运气"
                )
                suggestions.append("减少候选因子数量,或在独立样本上复现")
            if not checks["分层单调"]:
                concerns.append(
                    f"分层单调性仅 {ev.monotonicity:.0%},收益由个别极端档驱动,"
                    "IC 为正也未必能交易"
                )
            if not checks["扣费为正"]:
                concerns.append(
                    f"扣费后年化 {ev.net_annual:.2%}(换手 {ev.avg_turnover:.1%}/期),"
                    "毛收益被摩擦吃光"
                )
            if not checks["分段稳定"] and ev.subperiod_annual:
                segs = " / ".join(f"{s:.1%}" for s in ev.subperiod_annual)
                concerns.append(f"分段收益 {segs} 不稳定,警惕样本期特殊性")
            # 逐段递增是选池前视偏差的指纹 —— 单独点名
            subs = ev.subperiod_annual
            if len(subs) >= 3 and subs[0] <= 0 and all(
                subs[i] < subs[i + 1] for i in range(len(subs) - 1)
            ):
                concerns.append(
                    f"收益逐段递增且最早一段为负({subs[0]:.1%}→{subs[-1]:.1%}),"
                    "这是**前视偏差**的典型指纹 —— 标的池很可能用了未来信息"
                )
                suggestions.append(
                    f"标的池当前选法「{ev.universe_selection or '未记录'}」需改为时点重建,"
                    "并在无偏池上交叉验证"
                )
            if not ev.universe_selection:
                concerns.append("未记录标的池的选取方式,无法判断是否引入前视偏差")

        # ── 第三关:跑得赢"什么都不做"吗 ────────────────────────
        # 分不清 alpha 和 beta 是最容易犯的错。实测 09999 策略赚 21.7%
        # 看着不错,同期买入持有赚 25.6% —— 策略承担了择时风险、付了 48 笔
        # 交易成本,结果还不如躺着不动,却拿到评分 1.0 三角色全票通过。
        excess = m.excess_return
        if excess is not None:
            if excess <= 0:
                concerns.append(
                    f"跑输买入持有:策略 {m.total_return:.1%} vs 基准 "
                    f"{m.benchmark_return:.1%}(超额 {excess:+.1%}),"
                    "承担择时风险还不如躺着不动"
                )
                suggestions.append("先证明能跑赢买入持有,否则这个策略没有存在意义")
            elif m.sharpe < m.benchmark_sharpe:
                concerns.append(
                    f"超额收益 {excess:+.1%} 但风险调整后更差"
                    f"(夏普 {m.sharpe:.2f} < 基准 {m.benchmark_sharpe:.2f})"
                )

        # ── 第四关:样本外还成立吗 ──────────────────────────────
        # 最决定性的一关。实测 09618 全样本超额 +49%,拆开看:
        # 训练段 +70.8%、检验段 -29.2% —— 全部超额都在样本内,
        # 样本外一分没赚而躺着不动赚 28.5%。不拆就看不出来。
        oe = m.oos_excess
        if oe is not None and oe <= 0:
            concerns.append(
                f"样本外跑输基准:后30%区间策略 {m.oos_return:.1%} vs "
                f"基准 {m.oos_benchmark_return:.1%}(超额 {oe:+.1%})"
                " —— 全样本的优势只存在于样本内"
            )
            suggestions.append("边缘不能延续到样本外,不要上线;先查是否只在特定市况有效")

        # ── 第五关:是从多少个里挑出来的 ────────────────────────
        # 挑 55 个候选里的第一名必然乐观,与因子研究的多重检验同源。
        # 但这【不能】算作阻断性 concern:它对每个候选都成立,
        # 一旦计入 concerns,只要提案数 ≥10 就永远不会有策略通过 ——
        # 那是把关口焊死,不是提高门槛。改为压低信心 + 给出复现建议。
        selection_penalty = 0.0
        if m.n_candidates >= 10:
            selection_penalty = min(0.3, 0.05 * math.log10(m.n_candidates) * 4)
            suggestions.append(
                f"该策略是从 {m.n_candidates} 个候选中选出的最优,"
                "挑最大值必然高估 —— 上线前需在独立样本上复现"
            )

        # 交易次数太少 → 统计不显著,可能过拟合
        # 门槛设 10:实测中一个 5 笔交易、0% 胜率的失败策略曾被判 APPROVE
        # (旧门槛 `< 5` 对 n_trades==5 不成立),5 笔显然不足以支撑任何结论。
        if m.n_trades < 10:
            concerns.append(f"交易样本仅 {m.n_trades} 笔,统计不显著,疑似过拟合")
            suggestions.append("延长回测区间或放宽入场条件以获得更多样本")
        # 胜率极低 → 策略逻辑本身可能就是错的,不只是参数问题
        if m.n_trades > 0 and m.win_rate == 0.0:
            concerns.append(f"{m.n_trades} 笔交易胜率为 0,策略逻辑在该标的上完全失效")
            suggestions.append("先检验策略假设是否适用于该标的,而非调参")
        elif m.n_trades >= 10 and m.win_rate < 0.3:
            concerns.append(f"胜率仅 {m.win_rate:.0%},盈亏比需足够高才可能成立")
        # 夏普高得不真实
        if m.sharpe > 3.0:
            concerns.append(f"夏普 {m.sharpe:.2f} 偏高,警惕曲线拟合")
        # 参数是不是明显拍脑袋(用极端值粗判)
        for k, v in ctx.proposal.params.items():
            if v != 0 and (abs(v) > 100 or (0 < abs(v) < 1e-3)):
                concerns.append(f"参数 {k}={v} 取值可疑,像是手工凑出来的")

        # 数据不可信 = 一票否决。这类问题不是"改改参数"能解决的,
        # 在错的数据上调参只会得到更精致的错误。
        blocked = (
            (ctx.data_quality is not None and not ctx.data_quality.ok)
            or (ctx.evidence is not None and not ctx.evidence.verdict_ok)
            # 跑输基准同样是一票否决 —— 这不是"再调调参"的问题
            or (excess is not None and excess <= 0)
            # 样本外跑输 = 边缘不存在,同样一票否决
            or (oe is not None and oe <= 0)
        )
        if blocked:
            verdict = Verdict.REJECT
            confidence = 0.9
        else:
            verdict = Verdict.APPROVE if not concerns else Verdict.REVISE
            confidence = 0.8 if not concerns else max(0.4, 0.8 - 0.1 * len(concerns))
            confidence = round(max(0.3, confidence - selection_penalty), 2)
        return ReviewOpinion(
            role=ReviewRole.SKEPTIC,
            strategy_id=ctx.proposal.id,
            verdict=verdict,
            confidence=round(confidence, 2),
            concerns=concerns,
            suggestions=suggestions,
            rationale="先审数据与方法,再审过拟合与参数稳健性",
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
    """用 Claude 做真实评审。

    ⚠️ **API 漂移风险 —— 启用前必须跑 `scripts/verify_claude_judge.py`。**

    本类全程 `pragma: no cover`,单元测试只覆盖 MockLLMJudge。以下参数
    高度绑定 Anthropic SDK 的当前形态,任何一处变更都会让真实评审在
    **生产时**才失败,而 mock 测试永远是绿的:

      · 模型名 `claude-opus-4-8`
      · `thinking={"type": "adaptive"}`(旧版是 budget_tokens,已废弃)
      · `output_config={"format": {...}}`(旧版是顶层 output_format,已废弃)
      · 响应解析:从 `resp.content` 取 `type == "text"` 的块

    验收脚本会用真实 API 逐项校验这些契约,并额外验证判断力
    (一个 5 笔交易 / 0% 胜率的策略绝不该被判 APPROVE)。

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
