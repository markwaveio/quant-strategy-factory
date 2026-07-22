#!/usr/bin/env python3
"""ClaudeLLMJudge 真实验收 —— 必须在启用真实评审前跑通。

    python3 scripts/verify_claude_judge.py

**为什么必须单独验收:**
`ClaudeLLMJudge` 全程标着 `pragma: no cover`,单元测试只覆盖 `MockLLMJudge`。
它依赖的这些参数高度绑定 Anthropic SDK 的当前形态,任何一处漂移都会让
真实评审在**生产时**才炸,而 mock 测试永远绿:

  · 模型名          claude-opus-4-8
  · 自适应思考      thinking={"type": "adaptive"}
  · 结构化输出      output_config={"format": {"type": "json_schema", ...}}
  · 响应解析        resp.content 里取 type=="text" 的块

本脚本用**真实 API** 打一次,逐项校验契约。没有 ANTHROPIC_API_KEY 时
直接跳过并返回 0 —— 可以安全地放进 CI。
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from qsf.core.schema import (  # noqa: E402
    BacktestMetrics,
    ReviewRole,
    StrategyFamily,
    StrategyProposal,
    Verdict,
)
from qsf.evaluation.judge import ReviewContext  # noqa: E402

CHECKS: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    CHECKS.append((name, ok, detail))
    print(f"  {'✅' if ok else '❌'} {name}" + (f" — {detail}" if detail else ""))


def main() -> int:
    if not os.getenv("ANTHROPIC_API_KEY"):
        print("⏭  未设置 ANTHROPIC_API_KEY,跳过真实验收。")
        print("   启用真实评审前请务必设置并重跑本脚本。")
        return 0

    print("=== ClaudeLLMJudge 真实 API 验收 ===\n")

    # 1) 依赖与构造
    try:
        from qsf.evaluation.judge import ClaudeLLMJudge
        judge = ClaudeLLMJudge()
        check("SDK 可导入、客户端可构造", True)
    except Exception as e:
        check("SDK 可导入、客户端可构造", False, f"{type(e).__name__}: {e}")
        print("\n❌ 验收失败:先 pip install -e '.[llm]'")
        return 1

    # 2) 用一个「明显该被否决」的策略,同时验证契约与判断力
    ctx = ReviewContext(
        proposal=StrategyProposal(
            id="verify-1", family=StrategyFamily.TREND, symbol="TEST",
            rationale="验收用:样本不足且完全亏损的策略",
            params={"fast": 10, "slow": 30},
        ),
        metrics=BacktestMetrics(
            strategy_id="verify-1", total_return=-0.2192, annual_return=-0.2198,
            max_drawdown=-0.2231, sharpe=-1.91, win_rate=0.0, n_trades=5,
        ),
        market_features={"volatility": 0.39, "trend_strength": 0.13},
        similar_failures=0,
        max_drawdown_limit=0.25,
    )

    try:
        op = judge.review(ReviewRole.SKEPTIC, ctx)
    except Exception as e:
        check("真实 API 调用", False, f"{type(e).__name__}: {str(e)[:160]}")
        print("\n❌ 验收失败。常见原因:")
        print("   · 模型名漂移 → 核对 claude-opus-4-8 是否仍有效")
        print("   · thinking / output_config 参数形态变更 → 查 Anthropic 迁移文档")
        print("   · 余额或权限问题")
        return 1
    check("真实 API 调用", True)

    # 3) 逐项校验返回契约
    check("返回 ReviewOpinion 类型", hasattr(op, "verdict") and hasattr(op, "concerns"))
    check("verdict 是合法枚举", isinstance(op.verdict, Verdict), str(op.verdict))
    check("role 正确回填", op.role == ReviewRole.SKEPTIC)
    check("strategy_id 正确回填", op.strategy_id == "verify-1")
    check("confidence 在 [0,1]", 0.0 <= op.confidence <= 1.0, f"{op.confidence}")
    check("concerns 是列表", isinstance(op.concerns, list))

    # 4) 判断力:这个策略(5笔/0%胜率/负夏普)绝不该被 APPROVE
    sane = op.verdict != Verdict.APPROVE
    check("判断力:未把明显失败的策略判为 APPROVE", sane,
          f"实际 {op.verdict.value}")
    if op.concerns:
        print(f"     模型提出的问题:{op.concerns[0][:70]}")

    failed = [c for c in CHECKS if not c[1]]
    print(f"\n{'❌' if failed else '✅'} {len(CHECKS)-len(failed)}/{len(CHECKS)} 项通过")
    if failed:
        print("   未通过:" + "、".join(c[0] for c in failed))
        return 1
    print("   ClaudeLLMJudge 契约有效,可以启用真实评审。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
