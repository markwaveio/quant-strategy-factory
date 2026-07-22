"""参数搜索测试:参数必须来自搜索,且按稳健性(非单标的最优)挑选。"""

from datetime import datetime, timedelta

from qsf.backtest.engine import BacktestEngine
from qsf.core.config import BacktestConfig, Config, DataConfig
from qsf.core.pipeline import Pipeline
from qsf.core.schema import OHLCV, AssetClass, MarketSnapshot, PriceSeries
from qsf.strategy.search import ParamResult, ParamSearcher


def _snapshot(series: dict[str, list[float]]) -> MarketSnapshot:
    prices = {}
    for sym, closes in series.items():
        bars = [
            OHLCV(ts=datetime(2026, 1, 1) + timedelta(days=i), open=c, high=c * 1.01,
                  low=c * 0.99, close=c, volume=1e6)
            for i, c in enumerate(closes)
        ]
        prices[sym] = PriceSeries(symbol=sym, asset_class=AssetClass.A_SHARE, bars=bars)
    return MarketSnapshot(as_of=datetime.now(), prices=prices)


def test_sorts_by_coverage_then_worst_not_by_mean():
    """核心原则:覆盖率 > 最差 > 均值。单标的最优不得胜出。"""
    # A: 一个标的极好、其余为负 → 均值高但覆盖率低(典型过拟合)
    a = ParamResult(params={"x": 1}, per_symbol={"S1": 5.0, "S2": -1.0, "S3": -1.0})
    # B: 三个标的都小幅为正 → 均值低但覆盖率满
    b = ParamResult(params={"x": 2}, per_symbol={"S1": 0.4, "S2": 0.3, "S3": 0.5})
    assert a.mean > b.mean, "前提:A 的均值确实更高"
    assert b.sort_key() > a.sort_key(), "稳健的 B 必须排在过拟合的 A 之前"
    assert b.coverage == 1.0 and a.coverage < 0.5


def test_search_returns_params_and_marks_searched():
    snap = _snapshot({
        "S1": [100 + i * 0.5 for i in range(120)],
        "S2": [100 + i * 0.3 for i in range(120)],
    })
    searcher = ParamSearcher(BacktestEngine(BacktestConfig()), max_variants=12)
    params, info, results = searcher.search("trend", ["S1", "S2"], snap)
    assert params, "应返回一组参数"
    assert info.searched is True, "必须标记为「搜索得出」"
    assert info.n_evaluated > 1, "应评估多组参数"
    assert info.method == "grid"
    assert set(info.pool_symbols) == {"S1", "S2"}
    assert results


def test_invalid_combos_are_filtered():
    """快线不得 >= 慢线*0.8 —— 无意义组合应被剔除。"""
    snap = _snapshot({"S1": [100 + i * 0.5 for i in range(120)]})
    searcher = ParamSearcher(BacktestEngine(BacktestConfig()))
    _, _, results = searcher.search("trend", ["S1"], snap)
    for r in results:
        assert r.params["fast"] < r.params["slow"] * 0.8


def test_diagnose_flags_low_coverage():
    a = ParamResult(params={"x": 1}, per_symbol={"S1": 5.0, "S2": -1.0, "S3": -1.0},
                    n_trades={"S1": 30, "S2": 30, "S3": 30})
    warns = ParamSearcher.diagnose([a])
    assert any("通用性" in w for w in warns)


def test_pipeline_honours_param_search_flag():
    """开关必须真的改变产出的参数,否则 README 的说法就是空话。"""
    def run(flag):
        cfg = Config()
        cfg.data = DataConfig(symbols=["DEMO.A", "DEMO.B", "DEMO.C"],
                              lookback_days=200, seed=42)
        cfg.strategy.param_search = flag
        cfg.memory_path = "/tmp/qsf_test_mem.jsonl"
        r = Pipeline(cfg).run()
        return [p.params for p in r.proposals if p.family.value == "trend"][0]

    baseline, searched = run(False), run(True)
    assert baseline == {"fast": 10, "slow": 30}, "关闭时应为基线默认值"
    assert searched != baseline, "开启后参数必须来自搜索而非默认值"
