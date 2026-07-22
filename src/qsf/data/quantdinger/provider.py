"""QuantDingerDataProvider:用真实平台数据装配 MarketSnapshot。

满足 `qsf.data.provider.DataProvider` 协议,可直接替换 MockDataProvider。

标的来源两种:
- 配置里显式给了 symbols → 用配置的。
- 配置 symbols 为空 → 从 QuantDinger 的 watchlist(/api/market/watchlist/get)拉取
  用户已添加的自选,做到"盯我的 watchlist"。

装配流程与 MockDataProvider 完全一致(采集→标准化→特征提取),
所以洞察/策略/评估/回测/决策全部照常工作。
"""

from __future__ import annotations

from datetime import datetime, timedelta

from qsf.core.config import DataConfig
from qsf.core.schema import MarketSnapshot
from qsf.data.features import extract_features
from qsf.data.normalize import normalize_news, normalize_prices
from qsf.data.quantdinger.client import QuantDingerClient, QuantDingerError
from qsf.data.quantdinger.collectors import (
    QuantDingerNewsCollector,
    QuantDingerPriceCollector,
    QuantDingerReportCollector,
    _as_list,
    _first,
)


class QuantDingerDataProvider:
    def __init__(
        self,
        config: DataConfig,
        client: QuantDingerClient | None = None,
        *,
        interval: str = "1d",
        news_lang: str = "cn",
    ) -> None:
        self.config = config
        self.client = client or QuantDingerClient()
        self._price = QuantDingerPriceCollector(self.client, interval=interval)
        self._news = QuantDingerNewsCollector(self.client, lang=news_lang)
        self._report = QuantDingerReportCollector(self.client)

    def build_snapshot(self) -> MarketSnapshot:
        as_of = datetime.now()
        since = as_of - timedelta(days=self.config.lookback_days)
        symbols = self._resolve_symbols()

        if not symbols:
            raise QuantDingerError(
                "没有可用标的:配置未指定 symbols,且 watchlist 为空或拉取失败。"
                "请在 config.data.symbols 里显式指定,或先在 QuantDinger 添加自选。"
            )

        # 1) 行情:逐标的采集 + 标准化。单标的失败降级为跳过,不拖垮全局。
        prices = {}
        for sym in symbols:
            try:
                raw = self._price.collect(sym, self.config.lookback_days)
                clean = normalize_prices(raw)
                if clean.bars:  # 只保留真的有数据的
                    prices[sym] = clean
            except QuantDingerError:
                continue

        if not prices:
            raise QuantDingerError(
                f"所有标的行情均拉取失败(symbols={symbols})。"
                "请检查 QD_AGENT_TOKEN 是否有效、网关是否在 127.0.0.1:15000 运行。"
            )

        available = list(prices.keys())

        # 2) 新闻(全市场,按标的宽松关联)
        news = normalize_news(self._news.collect(available, since))

        # 3) 财报(当前为占位,返回 None)
        reports = {}
        for sym in available:
            rep = self._report.collect(sym)
            if rep is not None:
                reports[sym] = rep

        # 4) 特征提取(与 mock 完全一致的口径)
        features = {}
        for sym in available:
            sym_news = [n for n in news if sym in n.symbols]
            features[sym] = extract_features(
                as_of, prices[sym], sym_news, reports.get(sym)
            )

        return MarketSnapshot(
            as_of=as_of, prices=prices, news=news,
            reports=reports, features=features,
        )

    # 标的解析 ----------------------------------------------------------- #

    def _resolve_symbols(self) -> list[str]:
        # 配置显式指定优先
        if self.config.symbols:
            return list(self.config.symbols)
        # 否则从 watchlist 拉
        return self._fetch_watchlist_symbols()

    def _fetch_watchlist_symbols(self) -> list[str]:
        try:
            payload = self.client.get("/api/market/watchlist/get")
        except QuantDingerError:
            return []
        symbols: list[str] = []
        for row in _as_list(payload):
            if isinstance(row, str):
                symbols.append(row)
            elif isinstance(row, dict):
                sym = _first(row, "symbol", "code", "ticker", "name")
                if sym:
                    symbols.append(str(sym))
        # 去重保序
        return list(dict.fromkeys(symbols))
