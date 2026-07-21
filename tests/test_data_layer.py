"""数据层测试:采集可复现、标准化正确、特征算得出。"""

from qsf.core.config import DataConfig
from qsf.data.features import extract_features
from qsf.data.provider import MockDataProvider


def test_snapshot_is_reproducible():
    cfg = DataConfig(symbols=["DEMO.A", "DEMO.B"], lookback_days=120, seed=7)
    s1 = MockDataProvider(cfg).build_snapshot()
    s2 = MockDataProvider(cfg).build_snapshot()
    # 同 seed → 同收盘价序列
    assert s1.prices["DEMO.A"].closes == s2.prices["DEMO.A"].closes


def test_features_are_computed():
    cfg = DataConfig(symbols=["DEMO.A", "DEMO.C"], lookback_days=200, seed=42)
    snap = MockDataProvider(cfg).build_snapshot()
    fa = snap.features["DEMO.A"]
    assert fa.volatility is not None and fa.volatility >= 0
    assert -1.0 <= (fa.trend_strength or 0) <= 1.0
    # DEMO.C 带黑天鹅性格,应有黑天鹅分数
    fc = snap.features["DEMO.C"]
    assert fc.black_swan_score > 0


def test_black_swan_detected_from_news():
    # 构造一条带黑天鹅关键词的新闻,验证特征提取能识别
    from datetime import datetime

    from qsf.core.schema import OHLCV, AssetClass, NewsItem, PriceSeries

    bars = [
        OHLCV(ts=datetime(2026, 1, i + 1), open=100, high=101, low=99, close=100 + i, volume=1e6)
        for i in range(10)
    ]
    price = PriceSeries(symbol="X", asset_class=AssetClass.A_SHARE, bars=bars)
    news = [NewsItem(ts=datetime(2026, 1, 5), title="X 遭监管调查", body="退市风险", symbols=["X"])]
    feat = extract_features(datetime(2026, 1, 10), price, news, None)
    assert feat.black_swan_score > 0
    assert feat.sentiment < 0  # 负面
