"""回测层测试:指标计算正确、无未来函数、成本被计入。"""

import numpy as np

from qsf.backtest.engine import BacktestEngine
from qsf.backtest.metrics import annual_return, max_drawdown, sharpe, total_return
from qsf.core.config import BacktestConfig, DataConfig
from qsf.core.schema import StrategyFamily, StrategyProposal
from qsf.data.provider import MockDataProvider


def test_metrics_on_known_curve():
    equity = np.array([100.0, 110.0, 121.0])  # 每期 +10%
    assert abs(total_return(equity) - 0.21) < 1e-9
    assert max_drawdown(equity) == 0.0  # 单调上涨无回撤
    # 年化收益应为正
    assert annual_return(equity) > 0


def test_max_drawdown_negative():
    equity = np.array([100.0, 120.0, 90.0, 95.0])
    dd = max_drawdown(equity)
    assert dd < 0  # 从 120 跌到 90,回撤为负
    assert abs(dd - (90 / 120 - 1)) < 1e-9


def test_sharpe_zero_variance():
    # 恒定收益 → 标准差为 0 → 夏普定义为 0(避免除零)
    assert sharpe(np.array([0.01, 0.01, 0.01])) == 0.0


def test_backtest_runs_and_no_lookahead():
    dcfg = DataConfig(symbols=["DEMO.A"], lookback_days=200, seed=42)
    snap = MockDataProvider(dcfg).build_snapshot()
    proposal = StrategyProposal(
        id="t1", family=StrategyFamily.TREND, symbol="DEMO.A",
        rationale="test", params={"fast": 10, "slow": 30},
    )
    metrics = BacktestEngine(BacktestConfig()).run(proposal, snap)
    assert metrics.strategy_id == "t1"
    assert len(metrics.equity_curve) == len(snap.prices["DEMO.A"].bars)
    # 第一个净值应等于初始资金(第 0 根 bar 无持仓)
    assert abs(metrics.equity_curve[0] - BacktestConfig().initial_cash) < 1.0
