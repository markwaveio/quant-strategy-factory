"""QuantDinger 采集器:把网关返回的 JSON 解析成 QSF 的 schema 对象。

**容错是这一层的核心职责。** QuantDinger 是内部平台,响应字段没有稳定契约,
不同端点/版本可能返回 `ts` / `time` / `timestamp`、`open` / `o`、
`{data: [...]}` / `{list: [...]}` / 裸数组 等各种形态。这里用一组「取第一个存在的键」
的辅助函数把它们统统归一化,任何一个标的解析失败都不影响其它标的(降级而非崩溃)。

真实响应形态确认后,可以把这些容错收紧成精确解析;在那之前,宽松解析最稳。
"""

from __future__ import annotations

from datetime import datetime

from qsf.core.schema import (
    OHLCV,
    AssetClass,
    FinancialReport,
    NewsItem,
    PriceSeries,
)
from qsf.data.quantdinger.client import QuantDingerClient, QuantDingerError

# --------------------------------------------------------------------------- #
# 通用容错辅助                                                                  #
# --------------------------------------------------------------------------- #


def _first(d: dict, *keys, default=None):
    """按顺序返回第一个存在且非 None 的键值。"""
    for k in keys:
        if isinstance(d, dict) and d.get(k) is not None:
            return d[k]
    return default


def _as_list(payload) -> list:
    """把响应体归一化成列表。

    兼容:裸数组 / {data:[...]} / {list:[...]} / {items:[...]} / {result:[...]} /
    {bars:[...]} / {klines:[...]}。
    """
    if payload is None:
        return []
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        for key in ("data", "list", "items", "result", "results", "bars", "klines", "candles", "news"):
            v = payload.get(key)
            if isinstance(v, list):
                return v
        # 有的接口把列表再包一层 data.list
        inner = payload.get("data")
        if isinstance(inner, dict):
            return _as_list(inner)
    return []


def _to_ts(value) -> datetime:
    """把各种时间表示解析成 datetime。

    兼容:秒级/毫秒级 unix 时间戳(int/float/数字字符串)、ISO 字符串。
    """
    if isinstance(value, (int, float)):
        # 13 位是毫秒,10 位是秒
        return datetime.fromtimestamp(value / 1000 if value > 1e12 else value)
    if isinstance(value, str):
        s = value.strip()
        if s.isdigit():
            return _to_ts(int(s))
        # 常见 ISO 变体
        for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
            try:
                return datetime.strptime(s[: len(fmt) + 2].replace("Z", ""), fmt)
            except ValueError:
                continue
        try:
            return datetime.fromisoformat(s.replace("Z", "+00:00")).replace(tzinfo=None)
        except ValueError:
            pass
    return datetime.now()


def _to_float(value, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


# --------------------------------------------------------------------------- #
# 行情采集器                                                                    #
# --------------------------------------------------------------------------- #


class QuantDingerPriceCollector:
    """从 /api/indicator/kline 拉 OHLCV,解析成 PriceSeries。"""

    def __init__(self, client: QuantDingerClient, *, interval: str = "1d") -> None:
        self.client = client
        self.interval = interval

    def collect(self, symbol: str, lookback_days: int) -> PriceSeries:
        payload = self.client.get(
            "/api/indicator/kline",
            {"symbol": symbol, "interval": self.interval, "limit": lookback_days},
        )
        bars = self._parse_bars(_as_list(payload))
        asset_class = _infer_asset_class(symbol)
        return PriceSeries(symbol=symbol, asset_class=asset_class, bars=bars)

    @staticmethod
    def _parse_bars(rows: list) -> list[OHLCV]:
        bars: list[OHLCV] = []
        for row in rows:
            try:
                bars.append(_parse_one_bar(row))
            except (KeyError, TypeError, ValueError):
                # 单根异常不影响整体,跳过
                continue
        bars.sort(key=lambda b: b.ts)
        return bars


def _parse_one_bar(row) -> OHLCV:
    """支持两种常见形态:
    - dict: {ts/time/timestamp, open/o, high/h, low/l, close/c, volume/vol/v}
    - list/tuple: [ts, open, high, low, close, volume] (币安式)

    关键:识别不出收盘价的行(如结构完全不对的垃圾行)会抛 ValueError,
    由调用方跳过——绝不静默生成一根全 0 的假 K 线污染下游收益计算。
    """
    if isinstance(row, (list, tuple)):
        if len(row) < 5:
            raise ValueError("kline 数组长度不足")
        ts, o, h, low_, c = row[0], row[1], row[2], row[3], row[4]
        v = row[5] if len(row) > 5 else 0.0
        close = _to_float(c, default=-1.0)
        if close <= 0:
            raise ValueError("无有效收盘价")
        return OHLCV(
            ts=_to_ts(ts), open=_to_float(o, close), high=_to_float(h, close),
            low=_to_float(low_, close), close=close, volume=_to_float(v),
        )
    if not isinstance(row, dict):
        raise ValueError("kline 行既非数组也非对象")

    # 收盘价是必需项:识别不出就判定为垃圾行
    close_raw = _first(row, "close", "c", "closePrice")
    close = _to_float(close_raw, default=-1.0)
    if close_raw is None or close <= 0:
        raise ValueError("无有效收盘价")

    ts_raw = _first(row, "ts", "time", "timestamp", "t", "date", "openTime")
    if ts_raw is None:
        raise ValueError("无时间戳")

    # open/high/low 缺失时回退到 close(合理:等价于一根十字线)
    return OHLCV(
        ts=_to_ts(ts_raw),
        open=_to_float(_first(row, "open", "o", "openPrice"), close),
        high=_to_float(_first(row, "high", "h", "highPrice"), close),
        low=_to_float(_first(row, "low", "l", "lowPrice"), close),
        close=close,
        volume=_to_float(_first(row, "volume", "vol", "v", "amount", default=0.0)),
    )


def _infer_asset_class(symbol: str) -> AssetClass:
    s = symbol.upper()
    if s.endswith(("USDT", "USD", "BTC", "ETH")) or "-" in s and "USD" in s:
        return AssetClass.CRYPTO
    # A 股常见形态:6 位数字,或带 .SH/.SZ
    if s.split(".")[0].isdigit() and len(s.split(".")[0]) == 6:
        return AssetClass.A_SHARE
    return AssetClass.US_EQUITY


# --------------------------------------------------------------------------- #
# 新闻采集器                                                                    #
# --------------------------------------------------------------------------- #


class QuantDingerNewsCollector:
    """从 /api/global-market/news 拉新闻,解析成 NewsItem。

    平台新闻通常是全市场的,不按标的分。我们把 symbols 作为关键词做一次
    宽松匹配,标注每条新闻可能关联的标的,供特征层做情绪/黑天鹅提取。
    """

    def __init__(self, client: QuantDingerClient, *, lang: str = "cn", limit: int = 50) -> None:
        self.client = client
        self.lang = lang
        self.limit = limit

    def collect(self, symbols: list[str], since: datetime) -> list[NewsItem]:
        try:
            payload = self.client.get(
                "/api/global-market/news", {"lang": self.lang, "limit": self.limit}
            )
        except QuantDingerError:
            # 新闻拿不到不该让整条流水线挂掉,降级为空
            return []
        items: list[NewsItem] = []
        for row in _as_list(payload):
            item = self._parse_one(row, symbols)
            if item is not None and item.ts >= since:
                items.append(item)
        return items

    @staticmethod
    def _parse_one(row, symbols: list[str]) -> NewsItem | None:
        if not isinstance(row, dict):
            return None
        title = str(_first(row, "title", "headline", "name", default="")).strip()
        if not title:
            return None
        body = str(_first(row, "body", "summary", "content", "description", default=""))
        text = f"{title} {body}"
        # 宽松匹配:标的代码或其主体出现在文本里就关联
        related = [s for s in symbols if _symbol_matches(s, text)]
        return NewsItem(
            ts=_to_ts(_first(row, "ts", "time", "timestamp", "publishedAt", "date")),
            title=title,
            body=body,
            source=str(_first(row, "source", "provider", "site", default="quantdinger")),
            symbols=related,
            url=str(_first(row, "url", "link", default="")),
        )


def _symbol_matches(symbol: str, text: str) -> bool:
    """把 BTCUSDT / AAPL / 600519.SH 之类和新闻正文做宽松匹配。"""
    up = text.upper()
    s = symbol.upper()
    if s in up:
        return True
    # 去掉计价后缀再试(BTCUSDT -> BTC)
    for suffix in ("USDT", "USD", ".SH", ".SZ", ".HK"):
        if s.endswith(suffix):
            base = s[: -len(suffix)]
            if base and base in up:
                return True
    return False


# --------------------------------------------------------------------------- #
# 财报采集器(占位)                                                            #
# --------------------------------------------------------------------------- #


class QuantDingerReportCollector:
    """财报采集器。

    当前 QuantDinger 网关未暴露稳定的财报端点,因此这里返回 None(无财报)。
    整条流水线对"无财报"是容忍的:特征层的 fundamentals 会为空,不影响其它层。
    等平台有财报接口了,在这里补上即可,上层无感。
    """

    def __init__(self, client: QuantDingerClient) -> None:
        self.client = client

    def collect(self, symbol: str) -> FinancialReport | None:
        return None
