"""Mock 采集器:用可复现的随机过程生成行情 / 新闻 / 财报。

目的:让整条 8 层流水线在没有任何外部依赖、没有网络的情况下也能端到端跑通,
并且每个标的的「性格」不同(有的走趋势、有的震荡、有的被黑天鹅砸),
这样上层的洞察 / 策略 / 评估才有东西可做。

同一个 seed → 同一份数据,便于写测试和复现 bug。
"""

from __future__ import annotations

import random
from datetime import datetime, timedelta

from qsf.core.schema import (
    OHLCV,
    AssetClass,
    FinancialReport,
    NewsItem,
    PriceSeries,
)

# 给每个 demo 标的预设一种「行情性格」,让流水线的下游有区分度
_SYMBOL_PROFILES: dict[str, dict[str, float]] = {
    # drift: 日均漂移;vol: 日波动;black_swan_day: 在倒数第 N 天砸一根大阴线(0 表示不砸)
    "DEMO.A": {"drift": 0.0012, "vol": 0.010, "black_swan": 0.0},    # 稳健上涨,适合趋势
    "DEMO.B": {"drift": 0.0000, "vol": 0.022, "black_swan": 0.0},    # 高波动震荡,适合均值回归
    "DEMO.C": {"drift": 0.0005, "vol": 0.014, "black_swan": 12.0},   # 中途被黑天鹅砸,适合事件驱动
}
_DEFAULT_PROFILE = {"drift": 0.0003, "vol": 0.015, "black_swan": 0.0}

# 黑天鹅新闻模板,给带 black_swan 的标的配一条负面新闻
_BLACK_SWAN_HEADLINES = [
    "监管突发调查,{sym} 相关业务面临整改",
    "{sym} 主力工厂发生事故,产能受冲击",
    "评级机构下调 {sym} 展望至负面",
]
_POSITIVE_HEADLINES = [
    "{sym} 季度业绩超预期,机构上调目标价",
    "{sym} 获重要订单,新增产能落地",
    "行业景气度回升,{sym} 有望受益",
]


class MockPriceCollector:
    """几何布朗运动 + 可选黑天鹅跳空,生成日线。"""

    def __init__(self, seed: int = 42) -> None:
        self._seed = seed

    def _profile(self, symbol: str) -> dict[str, float]:
        return _SYMBOL_PROFILES.get(symbol, _DEFAULT_PROFILE)

    def collect(self, symbol: str, lookback_days: int) -> PriceSeries:
        # 每个 symbol 用独立但确定的随机流,互不干扰又可复现
        rng = random.Random(f"{self._seed}:{symbol}")
        prof = self._profile(symbol)
        drift, vol, swan_day = prof["drift"], prof["vol"], int(prof["black_swan"])

        price = 100.0
        bars: list[OHLCV] = []
        start = datetime.now() - timedelta(days=lookback_days)

        for i in range(lookback_days):
            # 到黑天鹅那天,叠加一个 -12% 的跳空冲击
            shock = 0.0
            if swan_day and i == lookback_days - swan_day:
                shock = -0.12

            ret = rng.gauss(drift, vol) + shock
            open_ = price
            close = max(0.5, open_ * (1 + ret))
            high = max(open_, close) * (1 + abs(rng.gauss(0, vol / 2)))
            low = min(open_, close) * (1 - abs(rng.gauss(0, vol / 2)))
            volume = rng.uniform(1e6, 5e6) * (2.0 if shock else 1.0)

            bars.append(
                OHLCV(
                    ts=start + timedelta(days=i),
                    open=round(open_, 4),
                    high=round(high, 4),
                    low=round(low, 4),
                    close=round(close, 4),
                    volume=round(volume, 0),
                )
            )
            price = close

        return PriceSeries(symbol=symbol, asset_class=AssetClass.A_SHARE, bars=bars)


class MockNewsCollector:
    """给每个标的配 1~2 条新闻;带黑天鹅性格的标的必出一条负面。"""

    def __init__(self, seed: int = 42) -> None:
        self._seed = seed

    def collect(self, symbols: list[str], since: datetime) -> list[NewsItem]:
        items: list[NewsItem] = []
        for symbol in symbols:
            rng = random.Random(f"{self._seed}:news:{symbol}")
            prof = _SYMBOL_PROFILES.get(symbol, _DEFAULT_PROFILE)

            if prof["black_swan"]:
                tpl = rng.choice(_BLACK_SWAN_HEADLINES)
                items.append(
                    NewsItem(
                        ts=datetime.now() - timedelta(days=int(prof["black_swan"])),
                        title=tpl.format(sym=symbol),
                        body="市场担忧情绪升温,短期不确定性显著上升。",
                        source="mock-wire",
                        symbols=[symbol],
                    )
                )
            else:
                tpl = rng.choice(_POSITIVE_HEADLINES)
                items.append(
                    NewsItem(
                        ts=datetime.now() - timedelta(days=rng.randint(1, 10)),
                        title=tpl.format(sym=symbol),
                        body="基本面边际改善,关注后续兑现。",
                        source="mock-wire",
                        symbols=[symbol],
                    )
                )
        return items


class MockReportCollector:
    """造一份季度财报快照。"""

    def __init__(self, seed: int = 42) -> None:
        self._seed = seed

    def collect(self, symbol: str) -> FinancialReport | None:
        rng = random.Random(f"{self._seed}:report:{symbol}")
        return FinancialReport(
            symbol=symbol,
            period="2026Q1",
            revenue=round(rng.uniform(1e8, 5e9), 0),
            net_profit=round(rng.uniform(-1e8, 8e8), 0),
            eps=round(rng.uniform(-0.5, 2.5), 2),
            roe=round(rng.uniform(-0.05, 0.25), 4),
            debt_ratio=round(rng.uniform(0.2, 0.7), 4),
        )
