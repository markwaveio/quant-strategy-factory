"""端到端示例:跑通 8 层流水线,并逐层打印中间产物。

    python examples/run_pipeline.py

这个脚本刻意把每一层的输出都打出来,方便理解数据是怎么一层层加工的。
全程 mock、离线、可复现,不需要任何 API key 或网络。
"""

from __future__ import annotations

from qsf.core.config import Config
from qsf.core.pipeline import Pipeline
from qsf.review.report import render_markdown


def main() -> None:
    config = Config()  # 默认 mock 配置
    pipeline = Pipeline(config)
    result = pipeline.run()

    print("=" * 70)
    print("① 数据层:MarketSnapshot")
    snap = result.snapshot
    print(f"  标的: {snap.symbols}")
    for sym in snap.symbols:
        f = snap.features[sym]
        print(
            f"  [{sym}] 波动率={f.volatility}  动量={f.momentum}  "
            f"趋势={f.trend_strength}  情绪={f.sentiment}  黑天鹅={f.black_swan_score}"
        )
    print(f"  新闻 {len(snap.news)} 条,财报 {len(snap.reports)} 份")

    print("=" * 70)
    print(f"② 洞察层:{len(result.insights)} 条洞察")
    for ins in result.insights:
        print(f"  [{ins.kind.value:12}] score={ins.score:.2f}  {ins.summary}")

    print("=" * 70)
    print(f"③ 策略生成层:{len(result.proposals)} 个提案")
    for p in result.proposals:
        print(f"  [{p.family.value:14}] {p.symbol}  params={p.params}  ({p.rationale})")

    print("=" * 70)
    print("④+⑥ 评估层(AI 挑战) + 回测层")
    for ev in result.evaluations:
        m = result.backtests[ev.strategy_id]
        print(f"  策略 {ev.strategy_id}")
        print(
            f"    回测: 收益={m.total_return:.1%} 夏普={m.sharpe:.2f} "
            f"回撤={m.max_drawdown:.1%} 交易={m.n_trades}笔"
        )
        for op in ev.opinions:
            print(f"    - {op.role.value:12} → {op.verdict.value:8} (信心 {op.confidence})")
            for c in op.concerns:
                print(f"        · {c}")
        print(f"    ==> 最终裁决: {ev.final_verdict.value}  (评分 {ev.aggregate_score})")

    print("=" * 70)
    print("⑤ 记忆层:累计经验")
    print(f"  {pipeline.memory_store.stats()}")

    print("=" * 70)
    print("⑦ 决策层 + ⑧ 复盘层:决策报告")
    print()
    print(render_markdown(result.report, result.review))


if __name__ == "__main__":
    main()
