"""向量化 **smoke backtest** 引擎。

⚠️ **这不是实盘级回测。** 它的定位是「快速淘汰明显不行的策略」——
用极低成本在几十毫秒内筛掉一眼就不成立的组合,让后续昂贵的真实回测
只跑在值得跑的候选上。**任何用于实际投资判断的绩效,都必须以
QuantDinger 服务端回测(4000+ 行,严格模式撮合)为准。**

流程:
1. 从提案实例化策略,拿到每根 bar 的目标仓位。
2. 目标仓位滞后一根 bar(避免用当根收盘信息交易未来),得到实际持仓。
3. 组合日收益 = 持仓 * 标的日收益 - 换仓成本(手续费+滑点)。
4. 由日收益推出净值曲线,再算各项绩效指标。

已建模:一根 bar 的信号滞后(防未来函数)、手续费、滑点、按交易段统计胜率。

**未建模的约束(用它的数字做决策前必须知道):**

| 缺失 | 后果 |
|---|---|
| 撮合价格 | 按收盘价成交,实盘应是下一根开盘或盘中撮合 |
| 停牌 / 跳空 | 停牌期间仍"持仓收益",跳空缺口未建模 |
| 复权 | 未处理除权除息,分红送股会造成虚假跳空 |
| 成交量容量 | 假设任意手数瞬时成交,大资金冲击成本为零 |
| 融资融券 | 做空一律可行,不校验券源、融券成本、强平 |
| 市场可做空性 | A股个股实际难做空,引擎却允许 |
| 组合层现金 | 单标的独立计算,不校验总资金占用 |
| 多标的相关性 | 各标的独立,组合风险被系统性低估 |
| 税费差异 | 只有统一 bps,未区分印花税/过户费/各市场规则 |

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

        # 买入持有基准 —— 满仓不动,同区间、同标的、扣一次开仓成本。
        # 策略跑不赢它就没有存在意义:承担同样的风险却少赚钱。
        bench_ret = asset_ret.copy()
        bench_ret[1] -= cost_rate if len(bench_ret) > 1 else 0.0
        bench_total = float(np.prod(1.0 + bench_ret) - 1.0)
        bsd = float(np.std(bench_ret[1:], ddof=1)) if len(bench_ret) > 2 else 0.0
        bench_sharpe = (
            float((np.mean(bench_ret[1:]) - self.config.risk_free_rate / 252)
                  / bsd * np.sqrt(252)) if bsd else 0.0
        )

        # 样本外分割:后 30% 完全不参与任何判断,只用来验证边缘是否延续。
        # 这是最便宜也最狠的证伪 —— 均值回归在震荡段亮眼、转单边就失效,
        # 只有分割才看得出来。
        k = int(len(port_ret) * 0.7)
        oos_r = oos_b = oos_s = None
        if len(port_ret) - k >= 20:
            op, ob = port_ret[k:], asset_ret[k:]
            oos_r = float(np.prod(1.0 + op) - 1.0)
            oos_b = float(np.prod(1.0 + ob) - 1.0)
            osd = float(np.std(op, ddof=1))
            oos_s = float((np.mean(op) - self.config.risk_free_rate / 252)
                          / osd * np.sqrt(252)) if osd else 0.0

        return BacktestMetrics(
            strategy_id=proposal.id,
            total_return=round(total_return(equity), 4),
            annual_return=round(annual_return(equity), 4),
            max_drawdown=round(max_drawdown(equity), 4),
            sharpe=round(sharpe(port_ret, self.config.risk_free_rate), 4),
            win_rate=round(win_rate(trade_returns), 4),
            n_trades=int(len(trade_returns)),
            equity_curve=[round(float(x), 2) for x in equity],
            # 最后一根 bar 的目标仓位 = 该策略"此刻"的方向,供决策层判断买/卖
            final_position=round(float(target[-1]), 4) if len(target) else 0.0,
            benchmark_return=round(bench_total, 4),
            benchmark_sharpe=round(bench_sharpe, 4),
            oos_return=round(oos_r, 4) if oos_r is not None else None,
            oos_benchmark_return=round(oos_b, 4) if oos_b is not None else None,
            oos_sharpe=round(oos_s, 4) if oos_s is not None else None,
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
