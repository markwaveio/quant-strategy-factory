#!/usr/bin/env python3
"""因子 IC 检验 —— 在构造策略之前,先验证信号到底有没有预测力。

    python3 scripts/factor_ic.py                      # 港股默认池
    python3 scripts/factor_ic.py --market USStock
    python3 scripts/factor_ic.py --horizon 5 20 60    # 自定义前瞻期

为什么要有这一步:
    真实的量化研究不是「想个策略 → 回测」,而是先问「这个信号有没有预测力」。
    直接构造策略回测,等于把【因子是否有效】和【策略实现是否正确】两个问题
    混在一起 —— 亏钱时你分不清是信号没用还是代码写错。

    IC 检验只需价格数据,不需要回测引擎,可以一次筛几十个因子。
    只有 IC 稳定显著的因子才配进入策略生成。

方法:
    对每个调仓日 t:
        x = 各标的的因子值(截面)
        y = 各标的从 t 到 t+N 的收益(截面)
        IC_t = spearman(x, y)          # 秩相关,对异常值稳健
    再看 IC 时间序列的统计量。

判读:
    |IC均值| > 0.02        因子有微弱预测力(业界常见门槛)
    |IC均值| > 0.05        较强
    ICIR = 均值/标准差      > 0.3 算稳定,> 0.5 较好
    IC>0 占比偏离 50%       方向一致性
    t 统计量 |t| > 2        统计显著

    ⚠️ 单期 IC 高不算数,要看【长期稳定】。IC 均值 0.03 但 ICIR 0.5,
       比 IC 均值 0.08 但 ICIR 0.1 有价值得多。
"""

from __future__ import annotations

import argparse
import json
import math
import os
import statistics as st
import sys
from datetime import datetime
from pathlib import Path

import tomllib

GATEWAY = os.getenv("QD_BASE_URL", "http://127.0.0.1:15000")

# 自建标的池 —— agent 的 search_symbols 只返回 10 个种子,
# 但 klines 对任意代码都可用(已实测),所以直接列出。
# 横截面 IC 至少需要 30+ 标的才有意义。
UNIVERSE = {
    "HKStock": [
        # 科技/互联网
        "00700", "09988", "03690", "01810", "09618", "01024", "09999", "00268",
        # 金融
        "00939", "01398", "03988", "02318", "01299", "00388", "02388", "00011",
        # 地产/基建
        "00001", "00016", "00688", "01109", "00012", "00083",
        # 能源/材料/工业
        "00883", "00386", "00857", "01088", "02899", "00019", "00669", "01766",
        # 消费/医药
        "00291", "00322", "01044", "02020", "06618", "01093", "01177", "02269",
        # 电信/公用
        "00762", "00941", "00728", "00002", "00003", "00006",
    ],
    "USStock": [
        "AAPL", "MSFT", "GOOGL", "AMZN", "META", "NVDA", "TSLA", "AVGO",
        "JPM", "BAC", "WFC", "GS", "MS", "V", "MA", "AXP",
        "JNJ", "UNH", "PFE", "MRK", "ABBV", "LLY", "TMO", "ABT",
        "XOM", "CVX", "COP", "SLB", "PG", "KO", "PEP", "WMT",
        "HD", "MCD", "NKE", "COST", "DIS", "CMCSA", "VZ", "T",
    ],
}


def _token() -> str:
    tok = os.getenv("QD_AGENT_TOKEN")
    if tok:
        return tok
    try:
        with open(os.path.expanduser("~/.codex/config.toml"), "rb") as f:
            return tomllib.load(f)["mcp_servers"]["quantdinger"]["env"]["QUANTDINGER_AGENT_TOKEN"]
    except Exception as e:
        raise SystemExit("找不到 QD_AGENT_TOKEN,请设置环境变量") from e


def fetch_opend(market: str, symbols: list[str], years: int):
    """从富途 OpenD 批量拉取 —— 工厂的统一数据源。

    相比 QuantDinger agent 通道:20年历史、前复权、**带 PE 与换手率**。
    额度:100 只不同股票/30 天(同一只重复拉不计)。
    """
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
    from datetime import timedelta

    from qsf.data.opend.client import OpenDClient
    end = datetime.now().date()
    start = end - timedelta(days=365 * years + 400)   # 多取一年做因子预热
    with OpenDClient() as c:
        used, remain = c.quota()
        print(f"  历史K线额度:已用 {used} / 剩余 {remain}", file=sys.stderr)
        return c.history_batch(
            market, symbols, start.isoformat(), end.isoformat(),
            on_progress=lambda i, n, ok: print(f"\r  拉取 {i}/{n} · 可用 {ok}",
                                               end="", file=sys.stderr))


# --------------------------------------------------------------------------- #
# 因子定义
#
# 约定:因子值【越大 = 预期收益越高】。所以"低波动因子"要取负号。
# 这样 IC 为正就统一表示「因子方向正确」。
# --------------------------------------------------------------------------- #

def _ret(c: list[float], n: int) -> float | None:
    """过去 n 日收益。"""
    if len(c) <= n or c[-n - 1] == 0:
        return None
    return c[-1] / c[-n - 1] - 1.0


def _vol(c: list[float], n: int) -> float | None:
    """近 n 日已实现波动(年化)。"""
    if len(c) <= n + 1:
        return None
    r = [(c[i] - c[i - 1]) / c[i - 1] for i in range(len(c) - n, len(c)) if c[i - 1]]
    if len(r) < 3:
        return None
    return st.stdev(r) * math.sqrt(252)


FACTORS: dict[str, callable] = {
    # A股实证:反转比动量有效 —— 过去跌得多的,未来涨得多
    "反转_20日":   lambda c, v: -x if (x := _ret(c, 20)) is not None else None,
    "反转_5日":    lambda c, v: -x if (x := _ret(c, 5)) is not None else None,
    # 动量 —— 研究说在A股偏弱,正好用来验证
    "动量_60日":   lambda c, v: _ret(c, 60),
    "动量_120日":  lambda c, v: _ret(c, 120),
    # 低波动异象 —— 实证有效因子之一
    "低波_20日":   lambda c, v: -x if (x := _vol(c, 20)) is not None else None,
    "低波_60日":   lambda c, v: -x if (x := _vol(c, 60)) is not None else None,
    # 流动性/规模代理 —— 成交额小的通常有溢价
    "低成交额":     lambda c, v: -math.log(m) if (m := (st.mean(v[-20:]) if len(v) >= 20 else 0)) > 0 else None,
    # 价格位置 —— "52周高点效应":越靠近前高,未来收益越高(文献中的著名异象)
    # 首版把方向定反了(写成"远离52周高"),实测 IC=-0.12 才发现,已更正。
    "靠近52周高":  lambda c, v: (c[-1] / max(c[-250:])) if len(c) >= 250 and max(c[-250:]) else None,
    # 横截面动量的另一种表达:剔除最近一个月的12月动量(经典 12-1 动量)
    "动量_12减1":  lambda c, v: (c[-21] / c[-250] - 1.0) if len(c) >= 250 and c[-250] else None,
}

# 需要 PE / 换手率的因子 —— 只有 OpenD 数据源能算(QuantDinger agent 通道没有)。
# A股实证中【价值】是最有效的因子之一,此前完全无法检验。
FACTORS_EXTRA = {
    # 低 PE = 价值。PE<=0(亏损)视为无效,不参与排序。
    "低PE_价值":  lambda pe, tr: (-pe if pe and pe > 0 else None),
    # 低换手 = 低关注度,常见有溢价
    "低换手率":   lambda pe, tr: (-tr if tr is not None and tr > 0 else None),
}


def spearman(x: list[float], y: list[float]) -> float | None:
    """秩相关。对异常值稳健,是 IC 的标准算法。"""
    n = len(x)
    if n < 5:
        return None

    def rank(v):
        order = sorted(range(n), key=lambda i: v[i])
        r = [0.0] * n
        i = 0
        while i < n:  # 处理并列名次
            j = i
            while j + 1 < n and v[order[j + 1]] == v[order[i]]:
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


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="因子 IC 检验")
    p.add_argument("--market", default="HKStock", choices=list(UNIVERSE))
    p.add_argument("--horizon", type=int, nargs="+", default=[5, 20],
                   help="前瞻期(交易日)")
    p.add_argument("--years", type=int, default=8, help="回溯几年(OpenD 最多20年)")
    p.add_argument("--step", type=int, default=5, help="调仓间隔(交易日)")
    p.add_argument("-o", "--output", help="结果写入 JSON")
    a = p.parse_args(argv)

    syms = UNIVERSE[a.market]
    print(f"=== 因子 IC 检验 · {a.market} · {len(syms)} 只标的 · 数据源 OpenD ===",
          file=sys.stderr)

    raw = fetch_opend(a.market, syms, a.years)
    print(file=sys.stderr)
    data: dict[str, dict] = {}
    for s, bars in raw.items():
        if len(bars) < 300:
            continue
        data[s] = {
            "close": [b.close for b in bars],
            "vol":   [b.turnover or b.volume for b in bars],   # 优先成交额
            "pe":    [b.pe_ratio for b in bars],
            "tr":    [b.turnover_rate for b in bars],
        }

    if len(data) < 15:
        print(f"❌ 可用标的仅 {len(data)} 只,横截面 IC 不可靠(需 ≥15)", file=sys.stderr)
        return 1

    # 对齐长度:取所有标的都有的最短长度
    n_bars = min(len(d["close"]) for d in data.values())
    print(f"  可用 {len(data)} 只 × {n_bars} 根日K\n", file=sys.stderr)

    results: dict = {}

    ALL = [(n, f, "price") for n, f in FACTORS.items()] + \
          [(n, f, "fund") for n, f in FACTORS_EXTRA.items()]

    for fname, ffn, kind in ALL:
        results[fname] = {}
        for h in a.horizon:
            ics: list[float] = []
            # 从 250(因子预热) 到 n_bars-h,按 step 取截面
            for t in range(250, n_bars - h, a.step):
                xs, ys = [], []
                for _s, d in data.items():
                    if kind == "fund":
                        fv = ffn(d["pe"][t], d["tr"][t])
                    else:
                        c, v = d["close"][:t + 1], d["vol"][:t + 1]
                        fv = ffn(c, v)
                    if fv is None or not math.isfinite(fv):
                        continue
                    fut = d["close"][t + h] / d["close"][t] - 1.0 if d["close"][t] else None
                    if fut is None or not math.isfinite(fut):
                        continue
                    xs.append(fv)
                    ys.append(fut)
                ic = spearman(xs, ys) if len(xs) >= 15 else None
                if ic is not None and math.isfinite(ic):
                    ics.append(ic)
            if len(ics) < 10:
                results[fname][h] = None
                continue
            m, sd = st.mean(ics), (st.stdev(ics) if len(ics) > 1 else 0.0)
            results[fname][h] = {
                "ic_mean": round(m, 4),
                "ic_std": round(sd, 4),
                "icir": round(m / sd, 3) if sd else 0.0,
                "positive_pct": round(sum(1 for x in ics if x > 0) / len(ics), 3),
                "t_stat": round(m / (sd / math.sqrt(len(ics))), 2) if sd else 0.0,
                "n_periods": len(ics),
            }

    # 输出
    print(f"{'因子':<14}{'前瞻':>5}{'IC均值':>9}{'ICIR':>8}{'正比例':>8}{'t值':>8}{'期数':>6}  判读")
    print("-" * 78)
    for fname, byh in results.items():
        for h, r in byh.items():
            if r is None:
                print(f"{fname:<14}{h:>4}日{'样本不足':>32}")
                continue
            v = abs(r["ic_mean"])
            verdict = ("⭐ 强" if v > 0.05 and abs(r["icir"]) > 0.3 else
                       "✓ 弱有效" if v > 0.02 and abs(r["t_stat"]) > 2 else
                       "— 无")
            print(f"{fname:<14}{h:>4}日{r['ic_mean']:>9.4f}{r['icir']:>8.3f}"
                  f"{r['positive_pct']:>8.1%}{r['t_stat']:>8.2f}{r['n_periods']:>6}  {verdict}")

    if a.output:
        with open(a.output, "w", encoding="utf-8") as f:
            json.dump({"market": a.market, "n_symbols": len(data),
                       "n_bars": n_bars, "generated_at": datetime.now().isoformat(),
                       "results": results}, f, ensure_ascii=False, indent=1)
        print(f"\n✅ 已写入 {a.output}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
