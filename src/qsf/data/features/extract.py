"""特征计算。

刻意用纯 numpy 实现,不依赖 ta-lib 之类的重库,保证开箱即跑。
情绪与黑天鹅采用「关键词词典」这种轻量方案作为占位;
真实场景可替换为模型打分(接口不变)。
"""

from __future__ import annotations

import math

import numpy as np

from qsf.core.schema import FeatureSet, FinancialReport, NewsItem, PriceSeries

# 轻量情绪词典(占位实现;真实可换成模型)
_NEG_WORDS = ["调查", "整改", "事故", "下调", "负面", "亏损", "担忧", "冲击", "违规", "退市"]
_POS_WORDS = ["超预期", "上调", "订单", "回升", "受益", "增长", "改善", "落地", "中标"]
_BLACK_SWAN_WORDS = ["调查", "事故", "退市", "违规", "崩", "暴雷", "冻结", "下调", "负面"]

_TRADING_DAYS = 252


def _annualized_vol(closes: list[float]) -> float:
    if len(closes) < 3:
        return 0.0
    arr = np.asarray(closes, dtype=float)
    rets = np.diff(arr) / arr[:-1]
    return float(np.std(rets, ddof=1) * math.sqrt(_TRADING_DAYS))


def _momentum(closes: list[float], window: int = 20) -> float:
    if len(closes) <= window:
        return 0.0
    return float(closes[-1] / closes[-window - 1] - 1.0)


def _trend_strength(closes: list[float], window: int = 20) -> float:
    """用收盘价对时间做线性回归,取标准化斜率作为趋势强度(-1~1)。"""
    if len(closes) < window:
        window = len(closes)
    if window < 3:
        return 0.0
    y = np.asarray(closes[-window:], dtype=float)
    x = np.arange(window, dtype=float)
    slope = np.polyfit(x, y, 1)[0]
    # 用价格均值归一,再压到 (-1,1)
    norm = slope / (np.mean(y) + 1e-9) * window
    return float(max(-1.0, min(1.0, norm)))


def _news_sentiment(news: list[NewsItem]) -> tuple[float, list[str], float]:
    """返回 (情绪分, 命中关键词, 黑天鹅强度)。"""
    if not news:
        return 0.0, [], 0.0

    pos = neg = swan = 0
    hit_keywords: list[str] = []
    for item in news:
        text = f"{item.title} {item.body}"
        for w in _POS_WORDS:
            if w in text:
                pos += 1
                hit_keywords.append(w)
        for w in _NEG_WORDS:
            if w in text:
                neg += 1
                hit_keywords.append(w)
        for w in _BLACK_SWAN_WORDS:
            if w in text:
                swan += 1

    total = pos + neg
    sentiment = 0.0 if total == 0 else (pos - neg) / total
    black_swan_score = min(1.0, swan / max(1, len(news)))
    # 去重保序
    uniq_keywords = list(dict.fromkeys(hit_keywords))
    return round(sentiment, 4), uniq_keywords, round(black_swan_score, 4)


def _fundamentals(report: FinancialReport | None) -> dict[str, float]:
    if report is None:
        return {}
    out: dict[str, float] = {}
    for name in ("eps", "roe", "debt_ratio"):
        val = getattr(report, name)
        if val is not None:
            out[name] = float(val)
    return out


def extract_features(
    as_of,
    price: PriceSeries,
    news: list[NewsItem],
    report: FinancialReport | None,
) -> FeatureSet:
    """把某标的的行情/新闻/财报浓缩成一份 FeatureSet。"""
    closes = price.closes
    sentiment, keywords, swan = _news_sentiment(news)
    return FeatureSet(
        symbol=price.symbol,
        as_of=as_of,
        volatility=round(_annualized_vol(closes), 4),
        momentum=round(_momentum(closes), 4),
        trend_strength=round(_trend_strength(closes), 4),
        sentiment=sentiment,
        keywords=keywords,
        black_swan_score=swan,
        fundamentals=_fundamentals(report),
    )
