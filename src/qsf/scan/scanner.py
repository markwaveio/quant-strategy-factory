from __future__ import annotations

from qsf.core.config import ScanConfig
from qsf.core.schema import (
    Insight,
    InsightKind,
    MarketSnapshot,
    ScanCandidate,
    StrategyFamily,
)


class DailyScanner:
    def __init__(self, config: ScanConfig) -> None:
        self.config = config

    def scan(
        self, snapshot: MarketSnapshot, insights: list[Insight] | None = None
    ) -> list[ScanCandidate]:
        insights_by_symbol: dict[str, list[Insight]] = {}
        for insight in insights or []:
            insights_by_symbol.setdefault(insight.symbol, []).append(insight)

        candidates = [
            c
            for c in (
                self._score_symbol(symbol, snapshot, insights_by_symbol.get(symbol, []))
                for symbol in snapshot.symbols
            )
            if c is not None and c.score >= self.config.min_score
        ]
        candidates.sort(key=lambda c: c.score, reverse=True)
        return candidates[: self.config.max_candidates]

    def _score_symbol(
        self, symbol: str, snapshot: MarketSnapshot, insights: list[Insight]
    ) -> ScanCandidate | None:
        feat = snapshot.features.get(symbol)
        price = snapshot.prices.get(symbol)
        if feat is None or price is None or len(price.bars) < 30:
            return None

        score = 0.0
        reasons: list[str] = []
        evidence: dict[str, float] = {}
        families: set[StrategyFamily] = set()

        momentum = feat.momentum or 0.0
        evidence["momentum"] = round(momentum, 4)
        if abs(momentum) >= self.config.momentum_threshold:
            score += min(0.35, abs(momentum) * 2.0)
            direction = "走强" if momentum > 0 else "走弱"
            reasons.append(f"{direction}:近端动量 {momentum:.1%}")
            families.add(StrategyFamily.TREND)

        volatility = feat.volatility or 0.0
        evidence["volatility"] = round(volatility, 4)
        if volatility >= self.config.volatility_threshold:
            score += min(0.25, volatility * 0.4)
            reasons.append(f"波动率抬升:{volatility:.1%}")
            families.add(StrategyFamily.MEAN_REVERSION)

        surge = self._volume_surge(price.closes, [b.volume for b in price.bars])
        evidence["volume_surge"] = round(surge, 4)
        if surge >= self.config.volume_surge_threshold:
            score += min(0.2, (surge - 1.0) * 0.12)
            reasons.append(f"成交量放大:{surge:.1f}x")
            families.add(StrategyFamily.TREND)

        near_high = self._near_high(price.closes)
        evidence["near_20d_high"] = round(near_high, 4)
        if near_high >= self.config.near_high_threshold and momentum > 0:
            score += 0.15
            reasons.append("接近20日新高")
            families.add(StrategyFamily.TREND)

        black_swan = feat.black_swan_score
        evidence["black_swan_score"] = round(black_swan, 4)
        if black_swan >= 0.3:
            score += min(0.25, black_swan * 0.25)
            reasons.append(f"事件冲击:黑天鹅强度 {black_swan:.2f}")
            families.add(StrategyFamily.NEWS_EVENT)

        for insight in insights:
            evidence[f"insight_{insight.kind.value}"] = round(insight.score, 4)
            if insight.kind in (InsightKind.STRENGTH, InsightKind.WEAKNESS):
                families.add(StrategyFamily.TREND)
            elif insight.kind == InsightKind.REGIME_SHIFT:
                families.add(StrategyFamily.MEAN_REVERSION)
            elif insight.kind == InsightKind.EVENT:
                families.add(StrategyFamily.NEWS_EVENT)
            score += min(0.15, insight.score * 0.08)

        if not reasons:
            return None
        return ScanCandidate(
            symbol=symbol,
            score=round(min(score, 1.0), 3),
            reasons=reasons,
            evidence=evidence,
            recommended_families=sorted(families, key=lambda f: f.value),
        )

    @staticmethod
    def _volume_surge(closes: list[float], volumes: list[float]) -> float:
        if len(closes) < 30 or len(volumes) < 30:
            return 0.0
        recent = volumes[-5:]
        base = volumes[-30:-5]
        base_avg = sum(base) / len(base) if base else 0.0
        recent_avg = sum(recent) / len(recent) if recent else 0.0
        return recent_avg / base_avg if base_avg > 0 else 0.0

    @staticmethod
    def _near_high(closes: list[float]) -> float:
        if len(closes) < 20:
            return 0.0
        high = max(closes[-20:])
        return closes[-1] / high if high > 0 else 0.0
