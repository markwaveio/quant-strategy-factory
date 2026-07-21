"""复盘层:每次流水线跑完后,回顾这一轮做得怎么样。

产出 ReviewNote:提案数/通过数/否决数、最佳策略、关键收获与改进方向。
另外提供把决策报告渲染成 Markdown 的工具,便于人阅读。
"""

from qsf.review.reviewer import Reviewer

__all__ = ["Reviewer"]
