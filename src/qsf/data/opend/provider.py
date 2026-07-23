"""OpenDDataProvider —— 满足 `DataProvider` 协议,可直接替换其它数据源。

装配出的 `MarketSnapshot` 与 Mock / QuantDinger 版结构完全一致,
下游 ②~⑧ 层无感。增量在于 `FeatureSet.fundamentals` 里多了
真实的 PE 与换手率(QuantDinger agent 通道拿不到)。
"""

from __future__ import annotations

from datetime import datetime, timedelta

from qsf.core.config import DataConfig
from qsf.core.schema import (
    OHLCV,
    AssetClass,
    DataQualityReport,
    MarketSnapshot,
    PriceSeries,
)
from qsf.data.features import extract_features
from qsf.data.normalize import normalize_prices
from qsf.data.opend.client import OpenDClient, OpenDError
from qsf.data.quality import check_panel

_ASSET_CLASS = {
    "HKStock": AssetClass.OTHER,
    "USStock": AssetClass.US_EQUITY,
    "CNStock": AssetClass.A_SHARE,
}


class OpenDDataProvider:
    """用富途 OpenD 作为唯一数据源。

    `config.symbols` 必填 —— OpenD 没有"默认标的池"接口,
    标的来源应是 `configs/watchlist.yaml` 或富途持仓。
    """

    def __init__(self, config: DataConfig, market: str = "HKStock",
                 client: OpenDClient | None = None, *, adjust: str = "qfq") -> None:
        # 这里刻意用 qfq,与研究脚本(hfq)不同,不是笔误:
        # 实时快照只回看约一年,qfq 在这个跨度不会出负价,
        # 而它保持"最新价 = 真实市价",下单价位判断才对得上。
        # 多年期因子研究必须用 hfq —— 见 client.history 的说明。
        self.config = config
        self.market = market
        self.adjust = adjust
        self._client = client
        # 最近一次 build_snapshot 的数据体检报告,供评估层的质疑者审问
        self.quality: DataQualityReport | None = None

    def build_snapshot(self) -> MarketSnapshot:
        if not self.config.symbols:
            raise OpenDError(
                "OpenD 数据源必须显式指定 symbols —— 它没有默认标的池接口。"
                "请在 config.data.symbols 或 configs/watchlist.yaml 里配置。"
            )
        as_of = datetime.now()
        # 多取 60 天日历日做特征预热(节假日会压缩交易日数)
        start = (as_of - timedelta(days=int(self.config.lookback_days * 1.6) + 60)).date()

        client = self._client or OpenDClient()
        owns = self._client is None
        try:
            raw = client.history_batch(
                self.market, list(self.config.symbols),
                start.isoformat(), as_of.date().isoformat(), adjust=self.adjust,
            )
        finally:
            if owns:
                client.close()

        if not raw:
            raise OpenDError(
                f"所有标的均拉取失败(market={self.market})。"
                "请确认 OpenD 已启动,且历史K线额度未耗尽(100只/30天)。"
            )

        ac = _ASSET_CLASS.get(self.market, AssetClass.OTHER)

        # 先体检,再清洗。顺序很重要:
        # `normalize_prices` 会**静默剔除**非法 bar —— 实测 01114 在前复权下
        # 1728 根只剩 715 根,序列残缺却毫无提示,下游照常出结论。
        # 体检必须在清洗之前跑,才能看见原始损坏程度并决定要不要阻断。
        rawseries = {
            sym: PriceSeries(
                symbol=sym, asset_class=ac,
                bars=[OHLCV(ts=datetime.strptime(b.date, "%Y-%m-%d"),
                            open=b.open, high=b.high, low=b.low,
                            close=b.close, volume=b.volume) for b in bars],
            )
            for sym, bars in raw.items()
        }
        healthy, self.quality = check_panel(rawseries)
        if not healthy:
            raise OpenDError(
                f"全部标的都没通过数据体检:\n{self.quality.render()}"
            )

        prices, features = {}, {}
        for sym, series in healthy.items():
            bars = raw[sym]
            clean = normalize_prices(series)
            if not clean.bars:
                continue
            prices[sym] = clean
            feat = extract_features(as_of, clean, [], None)
            # OpenD 的增量:真实 PE 与换手率
            last = bars[-1]
            if last.pe_ratio is not None:
                feat.fundamentals["pe"] = last.pe_ratio
            if last.turnover_rate is not None:
                feat.fundamentals["turnover_rate"] = last.turnover_rate
            features[sym] = feat

        return MarketSnapshot(as_of=as_of, prices=prices, news=[],
                              reports={}, features=features)
