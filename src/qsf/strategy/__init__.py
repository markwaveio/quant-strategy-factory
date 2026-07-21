"""策略生成层。

两件事:
1. 定义策略「信号契约」(`Strategy.signals`),回测层据此产生持仓。
2. 根据洞察,把合适的策略族实例化成 `StrategyProposal`(生成器)。

三个策略族:
- 趋势(TrendStrategy):双均线金叉/死叉。
- 均值回归(MeanReversionStrategy):布林带/z-score 反转。
- 新闻事件(NewsEventStrategy):黑天鹅/情绪触发的事件规避或反弹。
"""

from qsf.strategy.base import Strategy
from qsf.strategy.generator import StrategyGenerator

__all__ = ["Strategy", "StrategyGenerator"]
