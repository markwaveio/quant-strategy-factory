#!/usr/bin/env python3
"""因子分层回测 —— IC 显著之后的决定性检验:扣掉成本还剩多少?

    python3 scripts/factor_backtest.py --universe configs/universe.json --factor 反转_5日

为什么 IC 不够:
    IC 只说"因子值和未来收益有秩相关",没说这个相关能不能变成钱。
    两个致命缺口:
      1. **单调性** —— IC 高可能只由极端分位驱动(比如只有最垃圾的一档暴跌)。
         真因子应该 Q1>Q2>Q3>Q4>Q5 逐级递减,否则不可靠。
      2. **成本** —— 反转类因子换手极高。5日调仓 ≈ 每年 50 次,
         港股中小盘单边成本(佣金+印花税+滑点)约 0.15~0.25%,
         年化摩擦轻松吃掉 5~15 个点。毛收益再漂亮也可能是负的。

方法:
    每个调仓日按因子值排序分 N 档,等权持有到下期。
    多空组合 = 做多最优档 - 做空最差档。
    换手率按实际持仓变动计算,成本 = 换手 × 单边成本 × 2。
"""

from __future__ import annotations

import argparse
import json
import math
import statistics as st
import sys

sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent))
from factor_ic import (  # noqa: E402
    FACTORS,
    FACTORS_EXTRA,
    align_by_date,
    cross_section,
    fetch_opend,
)


def _quantile_split(pairs: list[tuple[str, float]], n_q: int) -> list[list[str]]:
    """按因子值升序分档。返回 n_q 个组,组内是标的代码。"""
    pairs = sorted(pairs, key=lambda p: p[1])
    size = len(pairs) / n_q
    return [[c for c, _ in pairs[int(i * size):int((i + 1) * size)]] for i in range(n_q)]


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="因子分层回测(含成本)")
    p.add_argument("--universe", help="标的池 JSON;不给则用 factor_ic 内置池")
    p.add_argument("--market", default="HKStock")
    p.add_argument("--factor", required=True, help="因子名,见 factor_ic.py 的 FACTORS")
    p.add_argument("--horizon", type=int, default=5, help="持有期=调仓间隔(交易日)")
    p.add_argument("--quantiles", type=int, default=5)
    p.add_argument("--years", type=int, default=8)
    p.add_argument("--cost-bps", type=float, default=20.0,
                   help="单边成本(基点)。港股中小盘含滑点约 15~25bp")
    p.add_argument("-o", "--output")
    a = p.parse_args(argv)

    allf = {**FACTORS, **FACTORS_EXTRA}
    if a.factor not in allf:
        print(f"❌ 未知因子 {a.factor}。可选: {', '.join(allf)}", file=sys.stderr)
        return 1
    is_fund = a.factor in FACTORS_EXTRA
    ffn = allf[a.factor]

    if a.universe:
        with open(a.universe, encoding="utf-8") as f:
            u = json.load(f)
    else:
        # 内置池按行业代表性挑选,不含"当前流动性"这一层前视偏差,
        # 适合用来交叉验证 build_universe.py 选出的池子是否被偏差污染
        from factor_ic import UNIVERSE
        u = {"market": a.market, "symbols": UNIVERSE[a.market], "cap_tier": "内置大盘池"}
    raw = fetch_opend(u["market"], u["symbols"], a.years)
    print(file=sys.stderr)

    raw = {s: b for s, b in raw.items() if len(b) >= 300}
    if len(raw) < 15:
        print(f"❌ 可用标的仅 {len(raw)} 只", file=sys.stderr)
        return 1
    # **按日期对齐** —— 同 factor_ic,截面必须是同一天的比较
    dates, data = align_by_date(raw)
    n_bars = len(dates)
    h = a.horizon
    print(f"  {len(data)} 只 · 交易日轴 {n_bars} 天 ({dates[0]} ~ {dates[-1]}) · "
          f"{a.factor} · {a.quantiles}档 · 持有{h}日 · 单边{a.cost_bps}bp\n", file=sys.stderr)

    # 逐期分档收益
    q_rets: list[list[float]] = [[] for _ in range(a.quantiles)]
    turnovers: list[float] = []
    extremes: list[tuple[float, str, str]] = []   # (收益, 标的, 日期) 用于诊断
    period_dates: list[str] = []
    prev_top: set[str] = set()
    prev_bot: set[str] = set()

    for t in range(250, n_bars - h, h):     # step=h → 不重叠
        pairs = cross_section(data, t, h, ffn, is_fund)
        if len(pairs) < a.quantiles * 3:
            continue

        groups = _quantile_split([(s, fv) for s, fv, _ in pairs], a.quantiles)
        if any(not g for g in groups):
            continue    # 有空档就整期跳过 —— 否则各档期数错位,Q5-Q1 会减错期
        fut_map = {s: r for s, _, r in pairs}
        for i, g in enumerate(groups):
            q_rets[i].append(st.mean(fut_map[s] for s in g))
        period_dates.append(dates[t])
        extremes.extend((r, s, dates[t]) for s, _, r in pairs if abs(r) > 0.5)

        # 换手:多头(最高档)+空头(最低档)的持仓变动
        top, bot = set(groups[-1]), set(groups[0])
        if prev_top:
            turnovers.append((len(top - prev_top) / max(len(top), 1)
                              + len(bot - prev_bot) / max(len(bot), 1)) / 2)
        prev_top, prev_bot = top, bot

    n = min(len(x) for x in q_rets)
    if n < 20:
        print(f"❌ 有效期数仅 {n},不足以判断", file=sys.stderr)
        return 1

    per_year = 252 / h
    avg_turnover = st.mean(turnovers) if turnovers else 1.0
    # 一次调仓的成本:换手比例 × 单边成本 × 2(卖出旧的+买入新的)
    cost_per_period = avg_turnover * (a.cost_bps / 1e4) * 2

    # 极端收益诊断 —— 均值被离群值绑架时,「年化 -98%」是伪结论而非经济现象
    if extremes:
        extremes.sort(key=lambda x: x[0])
        print(f"⚠️ {len(extremes)} 个单期收益 |r|>50%(占 {len(extremes)/(n*len(data)):.2%})"
              f",均值类指标会被它们主导:", file=sys.stderr)
        for r, s, d in extremes[:5]:
            print(f"     {d}  {s}  {r:+.1%}", file=sys.stderr)
        print(file=sys.stderr)

    print(f"{'档位':<8}{'每期均值':>10}{'中位数':>9}{'年化(中位)':>11}{'胜率':>8}{'夏普':>8}")
    print("-" * 56)
    stats = []
    for i, rs in enumerate(q_rets):
        m, sd = st.mean(rs), st.stdev(rs)
        med = st.median(rs)
        # 用中位数做年化 —— 对离群值稳健,单调性判断以它为准
        ann = (1 + med) ** per_year - 1
        shp = m / sd * math.sqrt(per_year) if sd else 0.0
        stats.append({"q": i + 1, "mean": round(m, 5), "median": round(med, 5),
                      "annual_median": round(ann, 4),
                      "annual_mean": round((1 + m) ** per_year - 1, 4),
                      "win": round(sum(1 for x in rs if x > 0) / len(rs), 3),
                      "sharpe": round(shp, 2)})
        tag = " ← 多头" if i == a.quantiles - 1 else (" ← 空头" if i == 0 else "")
        print(f"Q{i+1:<7}{m:>10.3%}{med:>9.3%}{ann:>11.2%}"
              f"{sum(1 for x in rs if x>0)/len(rs):>8.1%}{shp:>8.2f}{tag}")

    # 单调性:用中位数年化(抗离群),±1 为完美单调
    anns = [s["annual_median"] for s in stats]
    mono = sum(1 for i in range(len(anns) - 1) if anns[i + 1] > anns[i])
    mono_score = mono / (len(anns) - 1)

    # 多空组合
    ls = [q_rets[-1][i] - q_rets[0][i] for i in range(n)]
    m, sd = st.mean(ls), st.stdev(ls)
    gross_ann = (1 + m) ** per_year - 1
    gross_shp = m / sd * math.sqrt(per_year) if sd else 0.0
    net_m = m - cost_per_period
    net_ann = (1 + net_m) ** per_year - 1
    net_shp = net_m / sd * math.sqrt(per_year) if sd else 0.0
    t_stat = m / (sd / math.sqrt(n)) if sd else 0.0

    print(f"\n单调性: {mono}/{len(anns)-1} 级递增 ({mono_score:.0%})"
          f"{'  ✓ 单调' if mono_score >= 0.75 else '  ✗ 不单调 —— IC 可能由个别极端档驱动'}")
    print(f"\n多空组合(Q{a.quantiles} 多 / Q1 空) · {n} 期 · 年均调仓 {per_year:.0f} 次")
    print(f"  平均换手      {avg_turnover:>8.1%}/期")
    print(f"  毛年化        {gross_ann:>8.2%}   夏普 {gross_shp:.2f}   t={t_stat:.2f}")
    print(f"  年化成本      {-((1+cost_per_period)**per_year-1):>8.2%}")
    print(f"  ★ 净年化      {net_ann:>8.2%}   夏普 {net_shp:.2f}")

    # 分段稳健性 —— 最关键的证伪检验。
    #
    # 标的池是按【今天】的成交额挑的,而因子买的是【当年】低成交额的股票。
    # 一只沉寂多年后暴涨的股票,既会因为今天活跃而入选,又会在沉寂期
    # 因子值排名靠前,还恰好有漂亮的前瞻收益 —— 这是前视偏差在凭空制造 alpha。
    # 若效应主要由这个偏差驱动,它会【集中在样本后段】(暴涨发生的时候)。
    # 前后段都稳定为正,才说明因子可能是真的。
    print("\n分段稳健性(检验前视偏差 —— 若只有后段赚钱,大概率是选池偏差):")
    k = n // 3
    for i, (lo, hi) in enumerate([(0, k), (k, 2 * k), (2 * k, n)]):
        seg = ls[lo:hi]
        if len(seg) < 5:
            continue
        sm, ssd = st.mean(seg), (st.stdev(seg) if len(seg) > 1 else 0.0)
        sann = (1 + sm - cost_per_period) ** per_year - 1
        sshp = (sm - cost_per_period) / ssd * math.sqrt(per_year) if ssd else 0.0
        rng = (f"{period_dates[lo]}~{period_dates[hi-1]}"
               if len(period_dates) >= hi else f"第{lo}-{hi}期")
        print(f"  段{i+1} {rng}  净年化 {sann:>8.2%}  夏普 {sshp:>5.2f}  "
              f"{'✓' if sann > 0 else '✗'}")

    # 成本敏感性 —— 多大的成本会把这个因子吃穿?
    print("\n成本敏感性:")
    breakeven = m / (avg_turnover * 2) * 1e4 if avg_turnover else 0.0
    for bps in (5, 10, 15, 20, 30):
        c = avg_turnover * (bps / 1e4) * 2
        na = (1 + m - c) ** per_year - 1
        print(f"  单边 {bps:>2}bp → 净年化 {na:>7.2%}  {'✓' if na > 0 else '✗'}")
    print(f"  盈亏平衡点:单边 {breakeven:.1f}bp")

    if a.output:
        with open(a.output, "w", encoding="utf-8") as f:
            json.dump({"factor": a.factor, "market": u["market"],
                       "cap_tier": u.get("cap_tier"), "horizon": h,
                       "n_symbols": len(data), "n_periods": n,
                       "quantiles": stats, "monotonicity": round(mono_score, 3),
                       "avg_turnover": round(avg_turnover, 4),
                       "gross_annual": round(gross_ann, 4), "gross_sharpe": round(gross_shp, 2),
                       "net_annual": round(net_ann, 4), "net_sharpe": round(net_shp, 2),
                       "t_stat": round(t_stat, 2), "cost_bps": a.cost_bps,
                       "breakeven_bps": round(breakeven, 1)},
                      f, ensure_ascii=False, indent=1)
        print(f"\n✅ 已写入 {a.output}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
