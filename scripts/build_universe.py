#!/usr/bin/env python3
"""构建研究标的池 —— 用免费接口筛选,把稀缺的历史K线额度花在刀刃上。

    python3 scripts/build_universe.py --market HK --size 50 --cap-tier mid

关键约束:
    **历史K线额度只有 100 只不同股票 / 30 天。** 所以绝不能"先拉历史再筛选"。

    幸运的是:
      · get_stock_basicinfo  列出全市场      —— 不消耗额度
      · get_market_snapshot  给市值/成交额/PE —— 不消耗额度(已实测)

    因此先用快照免费筛出候选,再只对入选者拉历史。

筛选逻辑:
    1. 剔除停牌、退市、上市不足 N 年(需要足够历史算因子)
    2. 剔除成交额过低的 —— 流动性差的股票 IC 再高也交易不了,
       而且价格噪声大,会污染因子检验
    3. 按市值分档,取指定档位
    4. 档内按成交额降序取前 size 只(保证可交易性)
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from datetime import date, timedelta

# 市值分档(港元)。中小盘是因子研究的常见沃土 —— 大盘定价效率高,难有超额。
CAP_TIERS = {
    "mega":  (500e8, float("inf")),   # 500亿以上
    "large": (200e8, 500e8),
    "mid":   (50e8, 200e8),           # 50~200亿:中盘
    "small": (10e8, 50e8),            # 10~50亿:小盘
    "micro": (0, 10e8),               # 10亿以下:微盘(流动性风险大)
}


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="构建研究标的池")
    p.add_argument("--market", default="HK", choices=["HK", "US"])
    p.add_argument("--cap-tier", default="mid",
                   choices=list(CAP_TIERS) + ["mid_small"])
    p.add_argument("--size", type=int, default=50, help="取多少只")
    p.add_argument("--min-turnover", type=float, default=1e7,
                   help="最低日成交额(港元),过滤流动性陷阱")
    p.add_argument("--min-years", type=int, default=6,
                   help="最少上市年数(需足够历史算因子)")
    p.add_argument("-o", "--output", default="configs/universe.json")
    a = p.parse_args(argv)

    logging.disable(logging.CRITICAL)
    try:
        from futu import RET_OK, Market, OpenQuoteContext, SecurityType
    except ImportError:
        print("❌ 需要 futu-api:pip install -e '.[opend]'", file=sys.stderr)
        return 1

    ctx = OpenQuoteContext(host="127.0.0.1", port=11111)
    try:
        mkt = Market.HK if a.market == "HK" else Market.US
        ret, basic = ctx.get_stock_basicinfo(mkt, SecurityType.STOCK)
        if ret != RET_OK:
            print(f"❌ 股票列表获取失败: {basic}", file=sys.stderr)
            return 1
        print(f"全市场 {len(basic)} 只", file=sys.stderr)

        # ① 基础过滤(免费):退市 / 上市年限
        #
        # 坑1:`suspension` 在列表接口里是字符串 'N/A'(不是 bool)。非空字符串恒为真,
        #      直接 if 判断会把全市场滤光。真实停牌状态得看快照的 suspension 字段。
        # 坑2:`listing_date` 有 973 只是 '1970-01-01' —— epoch 占位符,不是真上市日。
        #      这些当"未知"处理:不直接排除,但排在已知满足年限的后面,
        #      因为额度稀缺,要优先给能确认有足够历史的标的。
        cutoff = (date.today() - timedelta(days=365 * a.min_years)).isoformat()
        cand, known = [], set()
        for _, r in basic.iterrows():
            if r.get("delisting") is True:
                continue
            ld = str(r.get("listing_date") or "")
            if ld == "1970-01-01" or not ld:
                cand.append(r["code"])          # 未知:留作后备
            elif ld <= cutoff:
                cand.append(r["code"])
                known.add(r["code"])            # 已确认上市 ≥ min_years
        print(f"  基础过滤后(未退市/上市≥{a.min_years}年或未知): {len(cand)} 只"
              f"(其中 {len(known)} 只上市日期已确认)", file=sys.stderr)

        # ② 快照筛选(免费) —— 分批,富途单次上限 400,限速 60次/30秒
        rows = []
        for i in range(0, len(cand), 200):
            chunk = cand[i:i + 200]
            ret, snap = ctx.get_market_snapshot(chunk)
            if ret != RET_OK:
                print(f"\n  ⚠️ 批次 {i} 失败: {snap}", file=sys.stderr)
                time.sleep(1.0)
                continue
            for _, r in snap.iterrows():
                if r.get("suspension") is True:   # 快照里才是真 bool
                    continue
                cap = r.get("total_market_val") or 0
                to = r.get("turnover") or 0
                if cap <= 0 or to < a.min_turnover:
                    continue
                rows.append({"code": r["code"], "cap": float(cap),
                             "turnover": float(to),
                             "pe": _f(r.get("pe_ttm_ratio")),
                             "listing_confirmed": r["code"] in known,
                             "name": ""})
            print(f"\r  快照 {min(i+200, len(cand))}/{len(cand)} · 合格 {len(rows)}",
                  end="", file=sys.stderr)
            time.sleep(0.6)  # 限速 60次/30秒
        print(file=sys.stderr)

        # 回填名称
        names = {r["code"]: r["name"] for _, r in basic.iterrows()}
        for x in rows:
            x["name"] = names.get(x["code"], "")

        # ③ 按市值分档
        if a.cap_tier == "mid_small":
            lo, hi = CAP_TIERS["small"][0], CAP_TIERS["mid"][1]
        else:
            lo, hi = CAP_TIERS[a.cap_tier]
        tier = [x for x in rows if lo <= x["cap"] < hi]
        print(f"  {a.cap_tier} 档({lo/1e8:.0f}~{hi/1e8:.0f}亿): {len(tier)} 只", file=sys.stderr)

        # ④ 排序:先要"上市日期已确认"的(额度稀缺,优先给能确认有历史的),
        #    再按成交额降序 —— 保证可交易性,也压低价格噪声
        tier.sort(key=lambda x: (not x["listing_confirmed"], -x["turnover"]))
        picked = tier[:a.size]
        n_conf = sum(1 for x in picked if x["listing_confirmed"])
        if n_conf < len(picked):
            print(f"  ⚠️ 入选中 {len(picked)-n_conf} 只上市日期未知(占位1970),"
                  f"历史可能不足 {a.min_years} 年", file=sys.stderr)

        out = {
            "market": "HKStock" if a.market == "HK" else "USStock",
            "cap_tier": a.cap_tier,
            "built_at": date.today().isoformat(),
            "filters": {"min_turnover": a.min_turnover, "min_years": a.min_years,
                        "cap_range": [lo, hi]},
            "symbols": [x["code"].split(".", 1)[1] for x in picked],
            "detail": picked,
        }
        with open(a.output, "w", encoding="utf-8") as f:
            json.dump(out, f, ensure_ascii=False, indent=1)

        print(f"\n✅ 选出 {len(picked)} 只 → {a.output}", file=sys.stderr)
        print(f"{'代码':<12}{'名称':<14}{'市值(亿)':>10}{'成交额(万)':>12}{'PE':>8}")
        for x in picked[:15]:
            pe = f"{x['pe']:.1f}" if x["pe"] else "—"
            print(f"{x['code']:<12}{x['name'][:12]:<14}{x['cap']/1e8:>10.1f}"
                  f"{x['turnover']/1e4:>12.0f}{pe:>8}")
        if len(picked) > 15:
            print(f"... 另 {len(picked)-15} 只")

        # 额度提醒 —— 这些标的还没拉历史,拉的时候才消耗
        ret, q = ctx.get_history_kl_quota(get_detail=False)
        if ret == RET_OK:
            print(f"\n历史K线额度:已用 {q[0]} / 剩余 {q[1]}", file=sys.stderr)
            if len(picked) > q[1]:
                print(f"⚠️  选出 {len(picked)} 只但额度只剩 {q[1]} —— "
                      f"拉历史时会有 {len(picked)-q[1]} 只失败", file=sys.stderr)
        return 0
    finally:
        ctx.close()


def _f(v):
    try:
        f = float(v)
        return f if f == f and f > 0 else None
    except (TypeError, ValueError):
        return None


if __name__ == "__main__":
    raise SystemExit(main())
