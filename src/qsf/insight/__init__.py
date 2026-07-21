"""洞察层:从 `MarketSnapshot` 里发现「值得注意的事」。

产出一组 `Insight`(异常 / 走强 / 走弱 / 结构切换 / 事件驱动),
供策略生成层作为「触发信号」。
"""

from qsf.insight.engine import InsightEngine

__all__ = ["InsightEngine"]
