"""向量化回测引擎。

流程:
1. 从提案实例化策略,拿到每根 bar 的目标仓位。
2. 目标仓位滞后一根 bar(避免用当根收盘信息交易未来),得到实际持仓。
3. 组合日收益 = 持仓 * 标的日收益 - 换仓成本(手续费+滑点)。
4. 由日收益推出净值曲线,再算各项绩效指标。

按「交易段」统计胜率:每次从空仓进场到出场算一笔交易。
"""

from __future__ import annotations

import numpy as np

from qsf.backtest.metrics import (
    annual_return,
    max_drawdown,
    sharpe,
    total_return,
    win_rate,
)
from qsf.core.config import BacktestConfig
from qsf.core.schema import BacktestMetrics, MarketSnapshot, StrategyProposal
from qsf.strategy.generator import build_strategy


class BacktestEngine:
    def __init__(self, config: BacktestConfig) -> None:
        self.config = config

    def run(
        self, proposal: StrategyProposal, snapshot: MarketSnapshot
    ) -> BacktestMetrics:
        price = snapshot.prices.get(proposal.symbol)
        if price is None or len(price.bars) < 5:
            return self._empty(proposal.id)

        closes = np.asarray(price.closes, dtype=float)
        strategy = build_strategy(proposal)
        target = np.clip(strategy.signals(closes), -1.0, 1.0)

        # 滞后一根 bar:t 时刻按 t-1 收盘后定的仓位持有
        position = np.zeros_like(target)
        position[1:] = target[:-1]

        # 标的日收益
        asset_ret = np.zeros_like(closes)
        asset_ret[1:] = closes[1:] / closes[:-1] - 1.0

        # 换仓成本:仓位变动量 * (手续费+滑点)
        cost_rate = (self.config.fee_bps + self.config.slippage_bps) / 1e4
        turnover = np.abs(np.diff(position, prepend=0.0))
        costs = turnover * cost_rate

        # 组合日收益与净值
        port_ret = position * asset_ret - costs
        equity = self.config.initial_cash * np.cumprod(1.0 + port_ret)

        # 交易分段统计胜率:持仓从 0 变非 0 视为开仓,回到 0 视为平仓
        trade_returns = self._segment_trade_returns(position, port_ret)

        return BacktestMetrics(
            strategy_id=proposal.id,
            total_return=round(total_return(equity), 4),
            annual_return=round(annual_return(equity), 4),
            max_drawdown=round(max_drawdown(equity), 4),
            sharpe=round(sharpe(port_ret, self.config.risk_free_rate), 4),
            win_rate=round(win_rate(trade_returns), 4),
            n_trades=int(len(trade_returns)),
            equity_curve=[round(float(x), 2) for x in equity],
        )

    @staticmethod
    def _segment_trade_returns(position: np.ndarray, port_ret: np.ndarray) -> np.ndarray:
        """把连续的非零持仓视为一笔交易,累计其区间复利收益。"""
        trades: list[float] = []
        in_trade = False
        acc = 1.0
        for pos, ret in zip(position, port_ret, strict=True):
            if pos != 0.0:
                in_trade = True
                acc *= 1.0 + ret
            elif in_trade:
                trades.append(acc - 1.0)
                in_trade = False
                acc = 1.0
        if in_trade:
            trades.append(acc - 1.0)
        return np.asarray(trades, dtype=float)

    def _empty(self, strategy_id: str) -> BacktestMetrics:
        return BacktestMetrics(
            strategy_id=strategy_id,
            total_return=0.0,
            annual_return=0.0,
            max_drawdown=0.0,
            sharpe=0.0,
            win_rate=0.0,
            n_trades=0,
            equity_curve=[self.config.initial_cash],
        )
