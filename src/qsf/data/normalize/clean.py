"""清洗与规整。"""

from __future__ import annotations

from qsf.core.schema import NewsItem, PriceSeries


def normalize_prices(series: PriceSeries) -> PriceSeries:
    """按时间排序、去重、剔除非法 K 线。"""
    seen: set = set()
    clean_bars = []
    for bar in sorted(series.bars, key=lambda b: b.ts):
        if bar.ts in seen:
            continue
        # 剔除明显非法的行情
        if bar.high < bar.low or min(bar.open, bar.close, bar.high, bar.low) <= 0:
            continue
        seen.add(bar.ts)
        clean_bars.append(bar)
    return PriceSeries(
        symbol=series.symbol, asset_class=series.asset_class, bars=clean_bars
    )


def normalize_news(items: list[NewsItem]) -> list[NewsItem]:
    """去重(按标题+来源)并按时间倒序。"""
    seen: set = set()
    out: list[NewsItem] = []
    for item in sorted(items, key=lambda n: n.ts, reverse=True):
        key = (item.title, item.source)
        if key in seen:
            continue
        seen.add(key)
        out.append(item)
    return out
