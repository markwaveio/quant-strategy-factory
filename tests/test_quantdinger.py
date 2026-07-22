"""QuantDinger 接入测试(不依赖网络/token)。

用一个 FakeClient 把各种可能的响应形态喂给采集器/provider,验证:
1. 解析容错:kline 支持 dict/list 两种形态、多种字段名与时间戳。
2. 新闻按标的宽松关联、情绪/黑天鹅特征能算出来。
3. provider 能从 watchlist 拉标的、装配出完整 MarketSnapshot。
4. 整条 8 层流水线能跑在"真实形态"的数据上(用 FakeClient 注入)。

这样即使没有真实 token,解析逻辑也在 CI 里被完整覆盖;真实联调只需换成真 client。
"""

from __future__ import annotations

from qsf.core.config import Config, DataConfig
from qsf.core.pipeline import Pipeline
from qsf.data.quantdinger.collectors import (
    QuantDingerNewsCollector,
    QuantDingerPriceCollector,
    _to_ts,
)
from qsf.data.quantdinger.provider import QuantDingerDataProvider


class FakeClient:
    """按路径返回预设响应,模拟 QuantDinger 网关。"""

    def __init__(self, responses: dict):
        self.responses = responses
        self.calls: list[tuple[str, dict | None]] = []
        self.has_token = True

    def get(self, path, params=None):
        self.calls.append((path, params))
        return self.responses.get(path)

    def post(self, path, body=None):
        self.calls.append((path, body))
        return self.responses.get(path)


# --- 时间戳解析 ----------------------------------------------------------- #


def test_ts_parsing_variants():
    from datetime import datetime

    assert _to_ts(1_700_000_000).year == 2023          # 秒级
    assert _to_ts(1_700_000_000_000).year == 2023      # 毫秒级
    assert _to_ts("2026-01-15").year == 2026            # ISO 日期
    assert isinstance(_to_ts("2026-01-15T09:30:00"), datetime)


# --- kline 解析容错 ------------------------------------------------------- #


def test_price_collector_dict_shape():
    client = FakeClient({
        "/api/indicator/kline": {"data": [
            {"ts": 1_700_000_000, "open": 100, "high": 105, "low": 98, "close": 103, "volume": 1000},
            {"ts": 1_700_086_400, "open": 103, "high": 108, "low": 102, "close": 107, "volume": 1200},
        ]}
    })
    coll = QuantDingerPriceCollector(client)  # type: ignore[arg-type]
    series = coll.collect("BTCUSDT", 250)
    assert len(series.bars) == 2
    assert series.bars[0].close == 103
    assert series.closes == [103, 107]


def test_price_collector_binance_list_shape():
    # 币安式:[openTime, open, high, low, close, volume, ...]
    client = FakeClient({
        "/api/indicator/kline": [
            [1_700_000_000_000, "100", "105", "98", "103", "1000"],
            [1_700_086_400_000, "103", "108", "102", "107", "1200"],
        ]
    })
    coll = QuantDingerPriceCollector(client)  # type: ignore[arg-type]
    series = coll.collect("ETHUSDT", 250)
    assert len(series.bars) == 2
    assert series.bars[1].close == 107
    # 字符串数字也应被正确解析
    assert series.bars[0].open == 100.0


def test_price_collector_short_field_names():
    # o/h/l/c/v 简写
    client = FakeClient({
        "/api/indicator/kline": {"klines": [
            {"t": 1_700_000_000, "o": 10, "h": 11, "l": 9, "c": 10.5, "v": 50},
        ]}
    })
    coll = QuantDingerPriceCollector(client)  # type: ignore[arg-type]
    series = coll.collect("AAPL", 250)
    assert series.bars[0].high == 11
    assert series.bars[0].close == 10.5


def test_price_collector_skips_bad_rows():
    client = FakeClient({
        "/api/indicator/kline": {"data": [
            {"ts": 1_700_000_000, "open": 100, "high": 105, "low": 98, "close": 103, "volume": 1000},
            {"garbage": "no ohlc here"},  # 坏行应被跳过而非崩溃
            None,
        ]}
    })
    coll = QuantDingerPriceCollector(client)  # type: ignore[arg-type]
    series = coll.collect("BTCUSDT", 250)
    assert len(series.bars) == 1  # 只有好的那根


# --- 新闻解析与关联 ------------------------------------------------------- #


def test_news_collector_associates_symbols():
    from datetime import datetime

    client = FakeClient({
        "/api/global-market/news": {"data": [
            {"title": "BTC 突破新高", "body": "市场情绪回升,受益板块领涨", "ts": 1_700_000_000},
            {"title": "某公司遭监管调查", "body": "AAPL 面临退市风险", "ts": 1_700_000_000},
            {"title": "无关新闻", "body": "天气不错", "ts": 1_700_000_000},
        ]}
    })
    coll = QuantDingerNewsCollector(client)  # type: ignore[arg-type]
    items = coll.collect(["BTCUSDT", "AAPL"], datetime(2020, 1, 1))
    assert len(items) == 3
    # BTC 新闻应关联到 BTCUSDT(去后缀匹配)
    btc_news = [n for n in items if "BTCUSDT" in n.symbols]
    assert len(btc_news) == 1
    # AAPL 黑天鹅新闻应关联到 AAPL
    aapl_news = [n for n in items if "AAPL" in n.symbols]
    assert len(aapl_news) == 1


# --- provider 装配 -------------------------------------------------------- #


def _sample_kline(n=60):
    """造 n 根有趋势的 K 线(dict 形态)。"""
    rows = []
    price = 100.0
    for i in range(n):
        price *= 1.005
        rows.append({
            "ts": 1_700_000_000 + i * 86400,
            "open": price, "high": price * 1.01,
            "low": price * 0.99, "close": price, "volume": 1e6,
        })
    return rows


def test_provider_resolves_watchlist_and_builds_snapshot():
    client = FakeClient({
        "/api/market/watchlist/get": {"data": [
            {"symbol": "BTCUSDT"}, {"symbol": "ETHUSDT"},
        ]},
        "/api/indicator/kline": {"data": _sample_kline()},
        "/api/global-market/news": {"data": [
            {"title": "BTC 获机构增持", "body": "利好", "ts": 1_700_000_000},
        ]},
    })
    cfg = DataConfig(mode="live", symbols=[], lookback_days=60)
    provider = QuantDingerDataProvider(cfg, client)  # type: ignore[arg-type]
    snap = provider.build_snapshot()

    # watchlist 里的两个标的都应装配出来
    assert set(snap.symbols) == {"BTCUSDT", "ETHUSDT"}
    # 每个标的都有特征
    for sym in snap.symbols:
        assert snap.features[sym].volatility is not None
    # 确认真的调了 watchlist 端点
    assert any(c[0] == "/api/market/watchlist/get" for c in client.calls)


def test_provider_explicit_symbols_skip_watchlist():
    client = FakeClient({
        "/api/indicator/kline": {"data": _sample_kline()},
        "/api/global-market/news": {"data": []},
    })
    cfg = DataConfig(mode="live", symbols=["BTCUSDT"], lookback_days=60)
    provider = QuantDingerDataProvider(cfg, client)  # type: ignore[arg-type]
    snap = provider.build_snapshot()
    assert snap.symbols == ["BTCUSDT"]
    # 显式给了 symbols,不应调 watchlist
    assert not any(c[0] == "/api/market/watchlist/get" for c in client.calls)


# --- 端到端:整条流水线跑在"真实形态"数据上 ------------------------------ #


def test_full_pipeline_on_quantdinger_shaped_data(tmp_path):
    client = FakeClient({
        "/api/market/watchlist/get": {"data": [{"symbol": "BTCUSDT"}, {"symbol": "ETHUSDT"}]},
        "/api/indicator/kline": {"data": _sample_kline(120)},
        "/api/global-market/news": {"data": [
            {"title": "BTC 突破关键阻力", "body": "机构增持,受益", "ts": 1_700_000_000},
        ]},
    })
    cfg = Config()
    cfg.data = DataConfig(mode="live", symbols=[], lookback_days=120)
    cfg.memory_path = str(tmp_path / "mem.jsonl")

    provider = QuantDingerDataProvider(cfg.data, client)  # type: ignore[arg-type]
    # 用注入的 provider 跑完整 8 层
    pipeline = Pipeline(cfg, data_provider=provider)
    result = pipeline.run()

    assert result.report is not None
    assert set(d.symbol for d in result.report.decisions) == {"BTCUSDT", "ETHUSDT"}
    assert len(result.evaluations) == len(result.proposals)
