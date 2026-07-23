"""研究证据包:把候选策略整理成可复验、可导出的研究产物。"""

from qsf.research.reporter import (
    ResearchReporter,
    render_research_markdown,
    strategy_payload,
    strategy_payload_json,
)

__all__ = [
    "ResearchReporter",
    "render_research_markdown",
    "strategy_payload",
    "strategy_payload_json",
]
