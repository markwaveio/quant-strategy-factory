"""三个策略族的具体信号实现。

刻意保持简单、透明——这样评估层的「质疑者」才有明确的东西可以挑刺
(例如:参数是不是拍脑袋、是不是过拟合)。
"""

from __future__ import annotations

import numpy as np

from qsf.strategy.base import Strategy


def _sma(x: np.ndarray, window: int) -> np.ndarray:
    """简单移动均线,前 window-1 个位置用累计均值兜底,避免 NaN。"""
    window = max(1, int(window))
    if window == 1 or len(x) < window:
        return x.copy()
    kernel = np.ones(window) / window
    sma = np.convolve(x, kernel, mode="valid")
    pad = np.array([np.mean(x[: i + 1]) for i in range(window - 1)])
    return np.concatenate([pad, sma])


class TrendStrategy(Strategy):
    """双均线趋势跟随:快线上穿慢线做多,下穿则空仓。"""

    def signals(self, closes: np.ndarray) -> np.ndarray:
        fast = int(self._param("fast", 10))
        slow = int(self._param("slow", 30))
        sma_fast = _sma(closes, fast)
        sma_slow = _sma(closes, slow)
        # 快线在慢线上方 → 持多(1),否则空仓(0)
        return np.where(sma_fast > sma_slow, 1.0, 0.0)


class MeanReversionStrategy(Strategy):
    """均值回归:价格偏离均线过远则反向押注回归。"""

    def signals(self, closes: np.ndarray) -> np.ndarray:
        window = int(self._param("window", 20))
        entry_z = self._param("entry_z", 1.0)
        sma = _sma(closes, window)
        # 滚动标准差(简单实现)
        std = np.array(
            [
                np.std(closes[max(0, i - window + 1) : i + 1], ddof=0) or 1e-9
                for i in range(len(closes))
            ]
        )
        z = (closes - sma) / std
        pos = np.zeros_like(closes)
        pos[z <= -entry_z] = 1.0   # 超跌 → 做多
        pos[z >= entry_z] = -1.0   # 超涨 → 做空
        return pos


class NewsEventStrategy(Strategy):
    """新闻事件驱动。

    简化建模:params 里带一个 `shock_index`(黑天鹅发生的相对位置,从末尾往前数)
    和 `mode`(0=规避:事件后空仓一段;1=反弹:事件后抄底做多一段)。
    没有明确事件位置时退化为空仓,交给评估层判断其价值。
    """

    def signals(self, closes: np.ndarray) -> np.ndarray:
        n = len(closes)
        pos = np.zeros(n)
        shock_index = int(self._param("shock_index", 0))  # 距末尾的天数
        hold = int(self._param("hold_days", 10))
        mode = int(self._param("mode", 1))
        if shock_index <= 0 or shock_index >= n:
            return pos
        start = n - shock_index
        end = min(n, start + hold)
        pos[start:end] = 1.0 if mode == 1 else 0.0  # 反弹做多 / 规避空仓
        return pos
