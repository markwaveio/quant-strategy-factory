"""决策层交易语义测试:动作必须反映实际持仓方向,不能按策略族查表。"""

from datetime import datetime

from qsf.backtest.engine import BacktestEngine
from qsf.core.config import BacktestConfig, EvaluationConfig
from qsf.core.schema import (
    OHLCV,
    Action,
    AssetClass,
    BacktestMetrics,
    EvaluationResult,
    MarketSnapshot,
    PriceSeries,
    StrategyFamily,
    StrategyProposal,
    Verdict,
)
from qsf.decision.decider import Decider


def _snap(prices):
    bars = [
        OHLCV(ts=datetime(2026, 1, 1) .replace(day=(i % 28) + 1), open=p, high=p * 1.02,
              low=p * 0.98, close=p, volume=1e6)
        for i, p in enumerate(prices)
    ]
    return MarketSnapshot(
        as_of=datetime.now(),
        prices={"X": PriceSeries(symbol="X", asset_class=AssetClass.A_SHARE, bars=bars)},
    )


def _approve(sid="s1"):
    return EvaluationResult(strategy_id=sid, opinions=[],
                            final_verdict=Verdict.APPROVE, aggregate_score=0.8)


def test_short_position_reports_sell_not_buy():
    """回归:均值回归在超涨时持空头,曾被 _FAMILY_ACTION 一律映射成 BUY。"""
    snap = _snap([100 + i * 2 for i in range(60)])          # 单调上涨 → 触发做空
    p = StrategyProposal(id="s1", family=StrategyFamily.MEAN_REVERSION, symbol="X",
                         rationale="超涨回归", params={"window": 20, "entry_z": 1.0})
    m = BacktestEngine(BacktestConfig()).run(p, snap)
    assert m.final_position < 0, "该行情下均值回归应持空头"

    rep = Decider(EvaluationConfig()).decide(snap, [], [p], [_approve()], {"s1": m})
    d = rep.decisions[0]
    assert d.action == Action.SELL, "空头仓位绝不能报成 BUY"
    assert any("空头" in r for r in d.reasons)


def test_flat_position_reports_hold_with_zero_weight():
    """无仓位时应观望,且建议仓位归零(不能给出入场权重)。"""
    snap = _snap([100.0] * 60)
    p = StrategyProposal(id="s2", family=StrategyFamily.TREND, symbol="X",
                         rationale="test", params={"fast": 10, "slow": 30})
    m = BacktestMetrics(strategy_id="s2", total_return=0.0, annual_return=0.0,
                        max_drawdown=0.0, sharpe=0.0, win_rate=0.0, n_trades=0,
                        final_position=0.0)
    rep = Decider(EvaluationConfig()).decide(snap, [], [p], [_approve("s2")], {"s2": m})
    d = rep.decisions[0]
    assert d.action == Action.HOLD
    assert d.suggested_weight == 0.0


def test_long_position_reports_buy():
    m = BacktestMetrics(strategy_id="s3", total_return=0.2, annual_return=0.15,
                        max_drawdown=-0.05, sharpe=1.2, win_rate=0.6, n_trades=12,
                        final_position=1.0)
    snap = _snap([100.0 + i for i in range(60)])
    p = StrategyProposal(id="s3", family=StrategyFamily.TREND, symbol="X",
                         rationale="test", params={"fast": 10, "slow": 30})
    rep = Decider(EvaluationConfig()).decide(snap, [], [p], [_approve("s3")], {"s3": m})
    assert rep.decisions[0].action == Action.BUY
