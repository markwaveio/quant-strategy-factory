"""回测层:把策略信号在历史行情上跑一遍,输出绩效指标。

向量化实现,计入手续费与滑点,产出复利收益、最大回撤、夏普、胜率等,
供评估层(风险官)和决策层引用。
"""

from qsf.backtest.engine import BacktestEngine

__all__ = ["BacktestEngine"]
