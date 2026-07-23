#!/usr/bin/env python3
"""状态条件化检验 —— 策略不是"永久有效",而是"在某些状态下有效"。

    python3 scripts/regime_conditional.py --signal mean_reversion

问题背景:
    实测 09618 均值回归:训练段超额 +70.8%,样本外 -29.2%。
    机制是震荡下跌段有效、转单边上涨即失效 —— 这不是"策略错了",
    是**策略有隐含前提而我们没写下来**。

    但单标的样本量太小,分不清"确实有状态依赖"和"纯粹是噪声"。
    所以把全池标的**池化**:43 只 × 多年,按状态分桶后每桶仍有上万个观测,
    才有效力回答「条件化能不能救回这个信号」。

做法:
    对每个 (标的, 交易日):
        pos    = 信号给出的目标仓位(滞后一根,不用未来信息)
        regime = 当日的市场状态(趋势强度 / 已实现波动 / 与均线距离)
        ret    = 次日收益
    按 regime 分桶,看每桶里 pos·ret 的均值与显著性。

    **关键对照:同时算基准(满仓不动)在同一桶里的表现。**
    信号在牛市桶里赚钱不算本事,得比同桶的买入持有强才算。
"""

from __future__ import annotations

import argparse
import json
import math
import statistics as st
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from factor_ic import UNIVERSE, fetch_opend  # noqa: E402


# --------------------------------------------------------------------------- #
# 信号(与工厂 strategy/families.py 同构,独立实现以免耦合)                        #
# --------------------------------------------------------------------------- #

def sig_mean_reversion(c: list[float], i: int, window: int = 20,
                       entry_z: float = 1.0) -> float | None:
    """均值回归:价格偏离均线 z 个标准差就反向持仓。"""
    if i < window:
        return None
    w = c[i - window + 1:i + 1]
    m = st.mean(w)
    sd = st.stdev(w) if len(w) > 1 else 0.0
    if not sd:
        return None
    z = (c[i] - m) / sd
    if z > entry_z:
        return -1.0
    if z < -entry_z:
        return 1.0
    return 0.0


def sig_trend(c: list[float], i: int, fast: int = 20, slow: int = 60) -> float | None:
    """趋势跟随:快线在慢线上方做多。"""
    if i < slow:
        return None
    f = st.mean(c[i - fast + 1:i + 1])
    s = st.mean(c[i - slow + 1:i + 1])
    return 1.0 if f > s else -1.0


SIGNALS = {"mean_reversion": sig_mean_reversion, "trend": sig_trend}


# --------------------------------------------------------------------------- #
# 状态变量                                                                      #
# --------------------------------------------------------------------------- #

def regime_trend_strength(c: list[float], i: int, n: int = 60) -> float | None:
    """趋势强度:方向性移动 / 总波动。接近 1 = 单边,接近 0 = 震荡。

    这是「均值回归应该在什么时候有效」最直接的度量。
    """
    if i < n:
        return None
    seg = c[i - n + 1:i + 1]
    net = abs(seg[-1] - seg[0])
    path = sum(abs(seg[j] - seg[j - 1]) for j in range(1, len(seg)))
    return net / path if path else None


def regime_volatility(c: list[float], i: int, n: int = 20) -> float | None:
    """已实现波动(年化)。"""
    if i < n + 1:
        return None
    r = [c[j] / c[j - 1] - 1 for j in range(i - n + 1, i + 1) if c[j - 1]]
    return st.stdev(r) * math.sqrt(252) if len(r) > 2 else None


REGIMES = {"趋势强度": regime_trend_strength, "波动率": regime_volatility}


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="状态条件化检验")
    p.add_argument("--signal", default="mean_reversion", choices=list(SIGNALS))
    p.add_argument("--market", default="HKStock")
    p.add_argument("--years", type=int, default=8)
    p.add_argument("--buckets", type=int, default=5)
    p.add_argument("--cost-bps", type=float, default=15.0, help="单边成本")
    p.add_argument("--long-only", action="store_true",
                   help="仓位限制为 {0,+1}:默认持有,只在看空时离场。\n"
                        "用于检验「跑输基准是不是因为做空放弃了正漂移」")
    p.add_argument("-o", "--output")
    a = p.parse_args(argv)

    raw = fetch_opend(a.market, UNIVERSE[a.market], a.years)
    print(file=sys.stderr)
    sigfn = SIGNALS[a.signal]

    # 逐标的、逐日收集 (状态值, 策略日收益, 基准日收益)
    obs: dict[str, list[tuple[float, float, float]]] = {k: [] for k in REGIMES}
    n_sym = 0
    for _sym, bars in raw.items():
        c = [b.close for b in bars]
        if len(c) < 300:
            continue
        n_sym += 1
        prev_pos = 0.0
        for i in range(len(c) - 1):
            pos = sigfn(c, i)
            if pos is None:
                continue
            if a.long_only:
                pos = max(pos, 0.0)     # 做空改为空仓,保留标的的正漂移
            ret = c[i + 1] / c[i] - 1.0
            # 换仓成本按仓位变动收取
            cost = abs(pos - prev_pos) * a.cost_bps / 1e4
            prev_pos = pos
            for rname, rfn in REGIMES.items():
                rv = rfn(c, i)
                if rv is not None and math.isfinite(rv):
                    obs[rname].append((rv, pos * ret - cost, ret))

    print(f"信号 {a.signal} · {n_sym} 只标的 · 单边 {a.cost_bps}bp\n")

    out: dict = {"signal": a.signal, "market": a.market, "n_symbols": n_sym,
                 "cost_bps": a.cost_bps, "regimes": {}}

    for rname, rows in obs.items():
        if len(rows) < a.buckets * 200:
            print(f"{rname}: 样本不足({len(rows)})")
            continue
        rows.sort(key=lambda x: x[0])
        size = len(rows) / a.buckets
        print(f"── 按【{rname}】分桶 · 共 {len(rows):,} 个观测 ──")
        print(f"{'桶':<6}{'状态区间':>18}{'策略日均':>11}{'基准日均':>11}"
              f"{'超额(年化)':>12}{'t值':>8}")
        buckets = []
        for b in range(a.buckets):
            seg = rows[int(b * size):int((b + 1) * size)]
            sr = [x[1] for x in seg]
            br = [x[2] for x in seg]
            diff = [x[1] - x[2] for x in seg]
            m = st.mean(diff)
            sd = st.stdev(diff) if len(diff) > 1 else 0.0
            t = m / (sd / math.sqrt(len(diff))) if sd else 0.0
            ann = (1 + m) ** 252 - 1
            print(f"Q{b+1:<5}{seg[0][0]:>8.3f}~{seg[-1][0]:<9.3f}"
                  f"{st.mean(sr):>11.4%}{st.mean(br):>11.4%}{ann:>12.2%}{t:>8.2f}")
            buckets.append({"q": b + 1, "lo": round(seg[0][0], 4),
                            "hi": round(seg[-1][0], 4), "n": len(seg),
                            "strategy_daily": round(st.mean(sr), 6),
                            "benchmark_daily": round(st.mean(br), 6),
                            "excess_annual": round(ann, 4), "t_stat": round(t, 2)})
        out["regimes"][rname] = buckets

        # 条件化是否有意义:最好桶与最差桶的差距,以及是否单调
        best = max(buckets, key=lambda x: x["excess_annual"])
        worst = min(buckets, key=lambda x: x["excess_annual"])
        # 多重检验:分了 buckets×len(REGIMES) 个桶
        n_tests = a.buckets * len(REGIMES)
        thr = 1.96 + 0.5 * math.log(n_tests)      # 粗略修正门槛
        print(f"  最好 Q{best['q']} {best['excess_annual']:+.2%}(t={best['t_stat']:.2f})"
              f" / 最差 Q{worst['q']} {worst['excess_annual']:+.2%}"
              f" / 修正门槛 |t|>{thr:.2f}")
        sig_buckets = [b for b in buckets if abs(b["t_stat"]) > thr]
        if sig_buckets:
            print(f"  ✓ {len(sig_buckets)} 个桶过修正门槛:"
                  f"{', '.join('Q%d(%+.1f%%)' % (b['q'], b['excess_annual']*100) for b in sig_buckets)}")
        else:
            print("  ✗ 没有任何一个状态桶过修正门槛 —— 条件化救不回这个信号")
        print()

    if a.output:
        Path(a.output).write_text(json.dumps(out, ensure_ascii=False, indent=1),
                                  encoding="utf-8")
        print(f"✅ 已写入 {a.output}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
