"""采集器协议。

采集器只负责「拿到原始数据」,不做任何清洗/特征。
清洗归标准化层(normalize),特征归特征层(features)。
"""

from __future__ import annotations

from datetime import datetime
from typing import Protocol, runtime_checkable

from qsf.core.schema import FinancialReport, NewsItem, PriceSeries


@runtime_checkable
class PriceCollector(Protocol):
    def collect(self, symbol: str, lookback_days: int) -> PriceSeries: ...


@runtime_checkable
class NewsCollector(Protocol):
    def collect(self, symbols: list[str], since: datetime) -> list[NewsItem]: ...


@runtime_checkable
class ReportCollector(Protocol):
    def collect(self, symbol: str) -> FinancialReport | None: ...
