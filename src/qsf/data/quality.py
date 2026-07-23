"""数据体检 —— 脏数据不许进下游。

为什么单独成层:
    2026-07 的因子研究里,前复权(减法)在巨额分红股上产出负价
    (01114 有 1013/1728 根 bar 为负,最低 -7.26),导致算出
    「单期收益 -613%」—— 而收益率的数学下界是 -100%。

    更危险的是 `normalize_prices` 会**静默剔除**这些 bar:
    1728 根只剩 715 根,序列残缺却没有任何提示,下游照常算指标出结论。
    静默降级比直接报错危险得多。

    所以体检必须:**先测量、再报告、超阈值就阻断**,而不是悄悄扔掉。

两个层级:
    check_series  单标的:价格合法性、收益率越界、停牌比例
    check_panel   多标的:bar 数量差异 → 能否按数组下标做横截面
"""

from __future__ import annotations

from qsf.core.schema import DataQualityReport, PriceSeries, QualityIssue

# 单标的允许的最大坏 bar 比例。超过就不是"个别异常",是取数方式错了。
MAX_BAD_RATIO = 0.02
# bar 数量的最大/最小比值。超过说明各标的历史长度差太多,
# 按数组下标对齐会让 index[t] 落在不同日期上 —— 实测差 3 年、落在 18 个日期。
MAX_BAR_COUNT_RATIO = 1.2


class DataQualityError(RuntimeError):
    """数据体检未通过。带上完整报告,便于定位是哪只、哪类问题。"""

    def __init__(self, report: DataQualityReport) -> None:
        super().__init__(report.render())
        self.report = report


def check_series(series: PriceSeries) -> list[QualityIssue]:
    """单标的体检。返回问题列表(空 = 干净)。"""
    issues: list[QualityIssue] = []
    bars = series.bars
    sym = series.symbol
    if not bars:
        return [QualityIssue(symbol=sym, code="empty", detail="无任何 K 线", fatal=True)]

    n = len(bars)

    # ① 非正价格 —— 几乎必然是复权方式选错(减法前复权 + 巨额分红)
    bad = sum(1 for b in bars
              if min(b.open, b.high, b.low, b.close) <= 0)
    if bad:
        issues.append(QualityIssue(
            symbol=sym, code="negative_price",
            detail=f"{bad}/{n} 根 bar 价格非正 —— 多年期研究应改用后复权(hfq)",
            fatal=bad / n > MAX_BAD_RATIO,
        ))

    # ② high < low —— 数据源本身错乱
    inverted = sum(1 for b in bars if b.high < b.low)
    if inverted:
        issues.append(QualityIssue(
            symbol=sym, code="inverted_hl",
            detail=f"{inverted}/{n} 根 bar 最高价低于最低价",
            fatal=True,
        ))

    # ③ 收益率越界 —— 下界是 -100%,低于它不是市场跌了,是数据错了
    impossible = 0
    for i in range(1, n):
        p0, p1 = bars[i - 1].close, bars[i].close
        if p0 > 0 and p1 / p0 - 1.0 <= -1.0:
            impossible += 1
    if impossible:
        issues.append(QualityIssue(
            symbol=sym, code="impossible_return",
            detail=f"{impossible} 处单日收益 ≤ -100%(数学不可能)",
            fatal=True,
        ))

    # ④ 长期停牌 —— 不致命,但会稀释截面并制造假的"低波动"
    halted = sum(1 for b in bars if b.volume <= 0)
    if halted / n > 0.1:
        issues.append(QualityIssue(
            symbol=sym, code="illiquid",
            detail=f"{halted}/{n} 根 bar 零成交,流动性存疑",
            fatal=False,
        ))
    return issues


def check_panel(prices: dict[str, PriceSeries], *,
                drop_fatal: bool = True) -> tuple[dict[str, PriceSeries], DataQualityReport]:
    """多标的体检。返回 (可用标的, 报告)。

    `drop_fatal=True` 时把致命标的剔除并继续;否则原样返回,由调用方决定。
    """
    rep = DataQualityReport(n_symbols=len(prices))
    kept: dict[str, PriceSeries] = {}

    for sym, series in prices.items():
        issues = check_series(series)
        rep.issues.extend(issues)
        fatal = [i for i in issues if i.fatal]
        if fatal and drop_fatal:
            rep.dropped[sym] = fatal[0].detail
            continue
        kept[sym] = series
    rep.n_dropped = len(rep.dropped)

    # ⑤ 横截面对齐风险 —— 这是最隐蔽也最致命的一条。
    #    各标的上市时间/停牌不同,bar 数量差异大时,`close[t]` 在不同标的上
    #    指向不同日期,所谓"截面"其实是拿 A 的 2018 年配 B 的 2019 年。
    counts = [len(s.bars) for s in kept.values()]
    if counts:
        rep.bar_count_min, rep.bar_count_max = min(counts), max(counts)
        ratio = rep.bar_count_max / max(rep.bar_count_min, 1)
        rep.index_alignment_safe = ratio <= MAX_BAR_COUNT_RATIO
        if not rep.index_alignment_safe:
            rep.issues.append(QualityIssue(
                symbol="<panel>", code="index_misalignment",
                detail=(f"bar 数量 {rep.bar_count_min}~{rep.bar_count_max}"
                        f"(比值 {ratio:.2f}),横截面必须按日期对齐"),
                fatal=False,   # 不阻断:按日期对齐即可解决,但必须让人看见
            ))
    return kept, rep


def require_clean(prices: dict[str, PriceSeries]) -> dict[str, PriceSeries]:
    """严格模式:有致命问题直接抛错。用在研究入口。"""
    kept, rep = check_panel(prices, drop_fatal=False)
    if not rep.ok:
        raise DataQualityError(rep)
    return kept
