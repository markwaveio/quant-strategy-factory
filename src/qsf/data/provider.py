"""数据层总装:采集 → 标准化 → 特征提取 → MarketSnapshot。

`DataProvider` 是上层唯一依赖的接口。默认 `MockDataProvider` 全程离线;
接入真实源时,实现另一个 DataProvider(或替换其中的采集器)即可。
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Protocol, runtime_checkable

from qsf.core.config import DataConfig
from qsf.core.schema import MarketSnapshot
from qsf.data.collectors.mock import (
    MockNewsCollector,
    MockPriceCollector,
    MockReportCollector,
)
from qsf.data.features import extract_features
from qsf.data.normalize import normalize_news, normalize_prices


@runtime_checkable
class DataProvider(Protocol):
    def build_snapshot(self) -> MarketSnapshot: ...


class MockDataProvider:
    """基于 mock 采集器的数据提供者(离线、可复现)。"""

    def __init__(self, config: DataConfig) -> None:
        self.config = config
        self._price = MockPriceCollector(seed=config.seed)
        self._news = MockNewsCollector(seed=config.seed)
        self._report = MockReportCollector(seed=config.seed)

    def build_snapshot(self) -> MarketSnapshot:
        as_of = datetime.now()
        since = as_of - timedelta(days=self.config.lookback_days)
        symbols = self.config.symbols

        # 1) 采集 + 标准化行情
        prices = {}
        for sym in symbols:
            raw = self._price.collect(sym, self.config.lookback_days)
            prices[sym] = normalize_prices(raw)

        # 2) 采集 + 标准化新闻
        news = normalize_news(self._news.collect(symbols, since))

        # 3) 采集财报
        reports = {}
        for sym in symbols:
            rep = self._report.collect(sym)
            if rep is not None:
                reports[sym] = rep

        # 4) 逐标的做特征提取
        features = {}
        for sym in symbols:
            sym_news = [n for n in news if sym in n.symbols]
            features[sym] = extract_features(
                as_of, prices[sym], sym_news, reports.get(sym)
            )

        return MarketSnapshot(
            as_of=as_of,
            prices=prices,
            news=news,
            reports=reports,
            features=features,
        )


def build_data_provider(config: DataConfig) -> DataProvider:
    """按 config.mode 选择数据源。

    - "mock"(默认):离线可复现的 MockDataProvider。
    - "opend"/"futu":富途 OpenD —— **推荐**,研究数据与执行场所同源,
      且带 PE/换手率(20年历史)。
    - "live"/"quantdinger":接入本地 QuantDinger 平台的真实数据。
      QuantDinger 依赖延迟导入,mock 模式完全不受影响。
    """
    mode = (config.mode or "mock").lower()
    if mode in ("opend", "futu"):
        # 富途 OpenD —— 统一数据底座(20年历史 + PE + 换手率,与执行场所同源)
        from qsf.data.opend import OpenDDataProvider

        return OpenDDataProvider(config, market=config.market)
    if mode in ("live", "quantdinger", "qd"):
        # 延迟导入:只有真正走 live 时才加载 QuantDinger 相关代码
        from qsf.data.quantdinger import QuantDingerDataProvider

        return QuantDingerDataProvider(config)
    return MockDataProvider(config)
