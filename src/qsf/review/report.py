"""把决策报告 + 复盘记录渲染成 Markdown,便于人阅读或落盘。"""

from __future__ import annotations

from qsf.core.schema import DecisionReport, ReviewNote


def render_markdown(report: DecisionReport, review: ReviewNote | None = None) -> str:
    lines: list[str] = []
    lines.append(f"# 量化决策报告 · {report.as_of:%Y-%m-%d %H:%M}")
    lines.append("")
    lines.append(f"> {report.market_summary}")
    lines.append("")

    if report.top_insights:
        lines.append("## 关键市场洞察")
        for ins in report.top_insights:
            lines.append(f"- {ins}")
        lines.append("")

    lines.append("## 决策建议")
    lines.append("")
    lines.append("| 标的 | 动作 | 信心 | 建议仓位 | 说明 |")
    lines.append("| --- | --- | ---: | ---: | --- |")
    for d in report.decisions:
        reason = d.reasons[0] if d.reasons else (d.warnings[0] if d.warnings else "")
        lines.append(
            f"| {d.symbol} | {d.action.value} | {d.conviction:.2f} | "
            f"{d.suggested_weight:.0%} | {reason} |"
        )
    lines.append("")

    # 展开每个决策的理由与警告
    for d in report.decisions:
        if not (d.reasons or d.warnings):
            continue
        lines.append(f"### {d.symbol} · {d.action.value}")
        for r in d.reasons:
            lines.append(f"- ✅ {r}")
        for w in d.warnings:
            lines.append(f"- ⚠️ {w}")
        lines.append("")

    if review is not None:
        lines.append("## 本轮复盘")
        lines.append(
            f"- 提案 {review.n_proposals}、通过 {review.n_approved}、否决 {review.n_rejected}"
        )
        if review.best_strategy_id:
            lines.append(
                f"- 最佳策略:`{review.best_strategy_id}`(夏普 {review.best_sharpe})"
            )
        for t in review.key_takeaways:
            lines.append(f"- 收获:{t}")
        for imp in review.improvements:
            lines.append(f"- 改进:{imp}")
        lines.append("")

    lines.append(f"---\n\n_{report.disclaimer}_")
    return "\n".join(lines)
