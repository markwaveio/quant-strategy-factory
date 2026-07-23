"""横截面分层回测 —— 因子能不能变成钱,由这里判决。

原先工厂只有单标的时序 smoke backtest,没有横截面能力,
所以因子研究只能在 scripts/ 里手写、游离在契约之外 ——
同一个对齐 bug 因此连续污染了两轮研究。这一层就是来堵这个洞的。

四道关(见 FactorEvidence.passed):
    ① 多重检验   检验 N 个因子就得按 N 修正门槛。22 个假设时 |t|>3.04,
                 不是 1.96 —— 否则光靠运气就会冒出约 1 个「显著」。
    ② 分层单调   IC 高可能只由极端一档驱动。Q1→Q5 要逐级递增。
    ③ 扣费为正   高换手因子的摩擦能吃掉全部毛收益。
    ④ 分段稳定   **前视偏差探针**。若标的池用了未来信息(比如按今天的
                 成交额挑池),收益会集中在样本后段。实测某因子分段为
                 -1.9% / +102% / +182%,逐段递增就是指纹;换成无偏池后
                 净年化从 78.7% 掉到 1.17%。

全部用中位数判单调 —— 小盘股收益极度右偏,均值会被个别离群值绑架。
"""

from __future__ import annotations

import math
import statistics as st
from collections.abc import Callable, Sequence

from qsf.core.schema import FactorEvidence, PriceSeries, QuantileStat

# 因子函数:(收盘序列, 成交额序列) → 因子值。约定【越大 = 预期收益越高】。
FactorFn = Callable[[Sequence[float], Sequence[float]], float | None]


class Panel:
    """按日期对齐的多标的面板。

    **横截面的前提是同一天比较。** 各标的上市时间、停牌天数不同,
    bar 数量实测能差 3 年;按数组下标对齐时 index[250] 会落在 18 个
    不同日期上,算出来的相关性毫无意义。
    """

    def __init__(self, prices: dict[str, PriceSeries]) -> None:
        dates = sorted({b.ts.date().isoformat()
                        for s in prices.values() for b in s.bars})
        pos = {d: i for i, d in enumerate(dates)}
        n = len(dates)
        self.dates = dates
        self.data: dict[str, dict] = {}

        for sym, series in prices.items():
            if not series.bars:
                continue
            close: list[float | None] = [None] * n
            vol: list[float | None] = [None] * n
            for b in series.bars:
                i = pos[b.ts.date().isoformat()]
                close[i], vol[i] = b.close, b.volume
            first = pos[series.bars[0].ts.date().isoformat()]
            last = pos[series.bars[-1].ts.date().isoformat()]
            # 生命周期内停牌前向填充;最后一根之后不填充
            # —— 退市/长停不能当作"零涨跌",那会凭空造出无风险持仓
            for i in range(first + 1, last + 1):
                if close[i] is None:
                    close[i], vol[i] = close[i - 1], 0.0
            self.data[sym] = {"close": close, "vol": vol,
                              "first": first, "last": last}

    def __len__(self) -> int:
        return len(self.dates)

    def cross_section(self, t: int, horizon: int, fn: FactorFn,
                      warmup: int = 250) -> list[tuple[str, float, float]]:
        """取 t 日截面:[(标的, 因子值, 未来 horizon 日收益)]。

        只纳入「t 日已上市且预热够、t+horizon 日仍在交易」的标的。
        """
        out = []
        for sym, d in self.data.items():
            if t - d["first"] < warmup or t + horizon > d["last"]:
                continue
            c0 = d["close"][t]
            if not c0:
                continue
            fv = fn(d["close"][d["first"]:t + 1], d["vol"][d["first"]:t + 1])
            if fv is None or not math.isfinite(fv):
                continue
            fut = d["close"][t + horizon] / c0 - 1.0
            if math.isfinite(fut):
                out.append((sym, fv, fut))
        return out


def spearman(x: Sequence[float], y: Sequence[float]) -> float | None:
    """秩相关。对异常值稳健 —— 但也正因如此看不见涨幅大小,别只看它。"""
    n = len(x)
    if n < 3:
        return None

    def rank(v):
        order = sorted(range(len(v)), key=lambda i: v[i])
        r = [0.0] * len(v)
        i = 0
        while i < len(order):          # 并列取平均秩
            j = i
            while j + 1 < len(order) and v[order[j + 1]] == v[order[i]]:
                j += 1
            avg = (i + j) / 2 + 1
            for k in range(i, j + 1):
                r[order[k]] = avg
            i = j + 1
        return r

    rx, ry = rank(x), rank(y)
    mx, my = st.mean(rx), st.mean(ry)
    num = sum((rx[i] - mx) * (ry[i] - my) for i in range(n))
    dx = math.sqrt(sum((rx[i] - mx) ** 2 for i in range(n)))
    dy = math.sqrt(sum((ry[i] - my) ** 2 for i in range(n)))
    return num / (dx * dy) if dx and dy else None


def _split(pairs: list[tuple[str, float]], n_q: int) -> list[list[str]]:
    pairs = sorted(pairs, key=lambda p: p[1])
    size = len(pairs) / n_q
    return [[c for c, _ in pairs[int(i * size):int((i + 1) * size)]]
            for i in range(n_q)]


def run_factor(panel: Panel, factor: str, fn: FactorFn, *,
               market: str = "", horizon: int = 20, quantiles: int = 5,
               cost_bps: float = 20.0, n_hypotheses: int = 1,
               universe_selection: str = "", universe_tier: str = "",
               warmup: int = 250) -> FactorEvidence:
    """跑完整证据链。步长 = 持有期,保证样本不重叠。

    重叠采样会让 IC 序列自相关、t 值虚高 ——
    实测 step=5/horizon=60 给出 t=11.89,改成不重叠只剩 2.31。
    """
    n_bars = len(panel)
    q_rets: list[list[float]] = [[] for _ in range(quantiles)]
    ics: list[float] = []
    turnovers: list[float] = []
    prev_top: set[str] = set()
    prev_bot: set[str] = set()
    used_dates: list[str] = []

    for t in range(warmup, n_bars - horizon, horizon):
        sec = panel.cross_section(t, horizon, fn, warmup)
        if len(sec) < quantiles * 3:
            continue
        groups = _split([(s, fv) for s, fv, _ in sec], quantiles)
        if any(not g for g in groups):
            continue        # 有空档就整期跳过,否则各档期数错位、Q5-Q1 减错期

        ic = spearman([fv for _, fv, _ in sec], [r for _, _, r in sec])
        if ic is not None and math.isfinite(ic):
            ics.append(ic)

        fut = {s: r for s, _, r in sec}
        for i, g in enumerate(groups):
            q_rets[i].append(st.mean(fut[s] for s in g))
        used_dates.append(panel.dates[t])

        top, bot = set(groups[-1]), set(groups[0])
        if prev_top:
            turnovers.append((len(top - prev_top) / max(len(top), 1)
                              + len(bot - prev_bot) / max(len(bot), 1)) / 2)
        prev_top, prev_bot = top, bot

    ev = FactorEvidence(
        factor=factor, market=market, universe_tier=universe_tier,
        n_symbols=len(panel.data), horizon=horizon,
        n_hypotheses=n_hypotheses, cost_bps=cost_bps,
        universe_selection=universe_selection,
    )
    n = min((len(x) for x in q_rets), default=0)
    if n < 20:
        return ev      # 样本不足,四道关自然全不过

    ev.n_periods = n
    ev.date_from, ev.date_to = used_dates[0], used_dates[-1]
    per_year = 252 / horizon

    if ics:
        m_ic = st.mean(ics)
        sd_ic = st.stdev(ics) if len(ics) > 1 else 0.0
        ev.ic_mean = round(m_ic, 4)
        ev.ic_ir = round(m_ic / sd_ic, 3) if sd_ic else 0.0

    for i, rs in enumerate(q_rets):
        med = st.median(rs)
        sd = st.stdev(rs) if len(rs) > 1 else 0.0
        ev.quantiles.append(QuantileStat(
            q=i + 1, mean=round(st.mean(rs), 5), median=round(med, 5),
            annual_median=round((1 + med) ** per_year - 1, 4),
            win_rate=round(sum(1 for x in rs if x > 0) / len(rs), 3),
            sharpe=round(st.mean(rs) / sd * math.sqrt(per_year), 2) if sd else 0.0,
        ))
    anns = [q.annual_median for q in ev.quantiles]
    ev.monotonicity = round(
        sum(1 for i in range(len(anns) - 1) if anns[i + 1] > anns[i]) / (len(anns) - 1), 3
    )

    ls = [q_rets[-1][i] - q_rets[0][i] for i in range(n)]
    m, sd = st.mean(ls), (st.stdev(ls) if len(ls) > 1 else 0.0)
    ev.avg_turnover = round(st.mean(turnovers), 4) if turnovers else 1.0
    cost = ev.avg_turnover * (cost_bps / 1e4) * 2
    ev.t_stat = round(m / (sd / math.sqrt(n)), 2) if sd else 0.0
    ev.gross_annual = round((1 + m) ** per_year - 1, 4)
    ev.net_annual = round((1 + m - cost) ** per_year - 1, 4)
    ev.net_sharpe = round((m - cost) / sd * math.sqrt(per_year), 2) if sd else 0.0
    ev.breakeven_bps = round(m / (ev.avg_turnover * 2) * 1e4, 1) if ev.avg_turnover else 0.0

    k = n // 3
    for lo, hi in ((0, k), (k, 2 * k), (2 * k, n)):
        seg = ls[lo:hi]
        if len(seg) >= 5:
            ev.subperiod_annual.append(
                round((1 + st.mean(seg) - cost) ** per_year - 1, 4))
    return ev
