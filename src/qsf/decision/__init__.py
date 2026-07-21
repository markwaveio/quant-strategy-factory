"""决策层:综合评审 + 回测,产出一份给人看的决策报告。

只把「通过评审」的策略转化为可执行建议(买/卖/持有/回避),
并给出信心、建议仓位、理由与警告。
"""

from qsf.decision.decider import Decider

__all__ = ["Decider"]
