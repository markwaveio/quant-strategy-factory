"""洞察引擎。

把「特征」翻译成「值得注意的事」。每条规则都产出一句人能读懂的 summary
和支撑它的量化 evidence,方便决策层和复盘层引用。

规则(阈值可调,先用经验值):
- 走强 / 走弱:趋势强度 + 动量同向且够大。
- 异常:近 5 日收益的 z-score 超过阈值(相对自身历史波动)。
- 结构切换:近端波动率显著高于远端(市场变喧闹)。
- 事件驱动:黑天鹅分数高,或情绪极端。
"""

from __future__ import annotations

import numpy as np

from qsf.core.schema import Insight, InsightKind, MarketSnapshot, PriceSeries


class InsightEngine:
    def __init__(
        self,
        *,
        strength_trend: float = 0.15,
        strength_momentum: float = 0.05,
        zscore_threshold: float = 2.0,
        regime_ratio: float = 1.5,
        black_swan_threshold: float = 0.3,
        sentiment_extreme: float = 0.5,
    ) -> None:
        self.strength_trend = strength_trend
        self.strength_momentum = strength_momentum
        self.zscore_threshold = zscore_threshold
        self.regime_ratio = regime_ratio
        self.black_swan_threshold = black_swan_threshold
        self.sentiment_extreme = sentiment_extreme

    def analyze(self, snapshot: MarketSnapshot) -> list[Insight]:
        insights: list[Insight] = []
        for symbol in snapshot.symbols:
            feat = snapshot.features.get(symbol)
            price = snapshot.prices[symbol]
            if feat is None:
                continue

            insights.extend(self._strength(symbol, snapshot, feat))
            anomaly = self._anomaly(symbol, snapshot, price)
            if anomaly:
                insights.append(anomaly)
            regime = self._regime_shift(symbol, snapshot, price)
            if regime:
                insights.append(regime)
            insights.extend(self._events(symbol, snapshot, feat))

        # 显著性从高到低
        insights.sort(key=lambda i: i.score, reverse=True)
        return insights

    # --- 具体规则 -------------------------------------------------------- #

    def _strength(self, symbol, snapshot, feat) -> list[Insight]:
        out: list[Insight] = []
        ts = feat.trend_strength or 0.0
        mom = feat.momentum or 0.0
        if ts >= self.strength_trend and mom >= self.strength_momentum:
            out.append(
                Insight(
                    kind=InsightKind.STRENGTH,
                    symbol=symbol,
                    as_of=snapshot.as_of,
                    score=min(1.0, (ts + mom * 2)),
                    summary=f"{symbol} 走强:趋势强度 {ts:.2f}、20日动量 {mom:.1%}",
                    evidence={"trend_strength": ts, "momentum": mom},
                )
            )
        elif ts <= -self.strength_trend and mom <= -self.strength_momentum:
            out.append(
                Insight(
                    kind=InsightKind.WEAKNESS,
                    symbol=symbol,
                    as_of=snapshot.as_of,
                    score=min(1.0, abs(ts) + abs(mom) * 2),
                    summary=f"{symbol} 走弱:趋势强度 {ts:.2f}、20日动量 {mom:.1%}",
                    evidence={"trend_strength": ts, "momentum": mom},
                )
            )
        return out

    def _anomaly(self, symbol, snapshot, price: PriceSeries) -> Insight | None:
        closes = np.asarray(price.closes, dtype=float)
        if len(closes) < 30:
            return None
        rets = np.diff(closes) / closes[:-1]
        recent = float(np.mean(rets[-5:]))
        mu, sigma = float(np.mean(rets)), float(np.std(rets, ddof=1))
        if sigma == 0:
            return None
        z = (recent - mu) / (sigma / np.sqrt(5))
        if abs(z) >= self.zscore_threshold:
            direction = "异常拉升" if z > 0 else "异常杀跌"
            return Insight(
                kind=InsightKind.ANOMALY,
                symbol=symbol,
                as_of=snapshot.as_of,
                score=min(1.0, abs(z) / 4),
                summary=f"{symbol} {direction}:近5日收益 z-score={z:.1f}",
                evidence={"zscore": round(z, 3), "recent_ret": round(recent, 4)},
            )
        return None

    def _regime_shift(self, symbol, snapshot, price: PriceSeries) -> Insight | None:
        closes = np.asarray(price.closes, dtype=float)
        if len(closes) < 60:
            return None
        rets = np.diff(closes) / closes[:-1]
        near = float(np.std(rets[-20:], ddof=1))
        far = float(np.std(rets[-60:-20], ddof=1))
        if far == 0:
            return None
        ratio = near / far
        if ratio >= self.regime_ratio:
            return Insight(
                kind=InsightKind.REGIME_SHIFT,
                symbol=symbol,
                as_of=snapshot.as_of,
                score=min(1.0, (ratio - 1) / 2),
                summary=f"{symbol} 波动率抬升:近端/远端波动比 {ratio:.2f},市场结构可能切换",
                evidence={"vol_ratio": round(ratio, 3), "near_vol": round(near, 4)},
            )
        return None

    def _events(self, symbol, snapshot, feat) -> list[Insight]:
        out: list[Insight] = []
        if feat.black_swan_score >= self.black_swan_threshold:
            out.append(
                Insight(
                    kind=InsightKind.EVENT,
                    symbol=symbol,
                    as_of=snapshot.as_of,
                    score=min(1.0, feat.black_swan_score + 0.3),
                    summary=f"{symbol} 黑天鹅信号:强度 {feat.black_swan_score:.2f},关键词 {feat.keywords}",
                    evidence={"black_swan": feat.black_swan_score},
                )
            )
        sent = feat.sentiment or 0.0
        if abs(sent) >= self.sentiment_extreme and feat.black_swan_score < self.black_swan_threshold:
            tone = "情绪极度乐观" if sent > 0 else "情绪极度悲观"
            out.append(
                Insight(
                    kind=InsightKind.EVENT,
                    symbol=symbol,
                    as_of=snapshot.as_of,
                    score=min(1.0, abs(sent)),
                    summary=f"{symbol} {tone}:情绪分 {sent:.2f}",
                    evidence={"sentiment": sent},
                )
            )
        return out
