"""绩效指标计算(纯函数,便于单测)。"""

from __future__ import annotations

import math

import numpy as np

_TRADING_DAYS = 252


def total_return(equity: np.ndarray) -> float:
    if len(equity) < 2 or equity[0] == 0:
        return 0.0
    return float(equity[-1] / equity[0] - 1.0)


def annual_return(equity: np.ndarray, periods_per_year: int = _TRADING_DAYS) -> float:
    if len(equity) < 2 or equity[0] <= 0:
        return 0.0
    n = len(equity) - 1
    growth = equity[-1] / equity[0]
    if growth <= 0:
        return -1.0
    return float(growth ** (periods_per_year / n) - 1.0)


def max_drawdown(equity: np.ndarray) -> float:
    """返回最大回撤(负数,例如 -0.2 表示最大回撤 20%)。"""
    if len(equity) == 0:
        return 0.0
    peak = np.maximum.accumulate(equity)
    dd = equity / peak - 1.0
    return float(dd.min())


def sharpe(returns: np.ndarray, risk_free_rate: float = 0.02,
           periods_per_year: int = _TRADING_DAYS) -> float:
    """年化夏普比率。returns 为逐期收益率序列。"""
    if len(returns) < 2:
        return 0.0
    rf_per = risk_free_rate / periods_per_year
    excess = returns - rf_per
    std = np.std(excess, ddof=1)
    if std == 0:
        return 0.0
    return float(np.mean(excess) / std * math.sqrt(periods_per_year))


def win_rate(trade_returns: np.ndarray) -> float:
    if len(trade_returns) == 0:
        return 0.0
    return float(np.mean(trade_returns > 0))
