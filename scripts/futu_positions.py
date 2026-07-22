#!/usr/bin/env python3
"""从富途 OpenD 读取持仓,输出成每日扫描能直接用的 JSON。

    python3 scripts/futu_positions.py              # 用 configs/opend.yaml
    python3 scripts/futu_positions.py --env REAL   # 临时覆盖交易环境
    python3 scripts/futu_positions.py --quiet      # 只输出 JSON,无提示

只读:仅调用 position_list_query,不下单、不改单、不解锁交易。

输出格式(直接对应扫描器的标的池第①层):
    {
      "as_of": "...",
      "trd_env": "SIMULATE",
      "positions": [
        {"market": "HKStock", "symbol": "00700", "name": "腾讯控股",
         "qty": 100.0, "cost": 459.6, "pl_ratio": -4.13, "futu_code": "HK.00700"}
      ],
      "errors": []
    }
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import datetime
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG = REPO / "configs" / "opend.yaml"


def load_config(path: Path) -> dict:
    if not path.exists():
        raise SystemExit(f"找不到配置: {path}")
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def to_qd_symbol(futu_code: str, market_map: dict) -> tuple[str, str]:
    """富途代码 → (QuantDinger 市场, 标的代码)。

    HK.00700 → ("HKStock", "00700");无法识别时原样返回,由调用方决定怎么办。
    """
    if "." in futu_code:
        prefix, sym = futu_code.split(".", 1)
        return market_map.get(prefix, prefix), sym
    return "", futu_code


def fetch_positions(cfg: dict, env_override: str | None = None) -> dict:
    # futu SDK 会往 stdout 打大量连接日志,污染 JSON 输出 —— 全局压掉
    logging.disable(logging.CRITICAL)

    try:
        from futu import (
            RET_OK,
            OpenSecTradeContext,
            SecurityFirm,
            TrdEnv,
            TrdMarket,
        )
    except ImportError as e:
        raise SystemExit(
            "未安装 futu-api。执行:pip install futu-api"
        ) from e

    od = cfg.get("opend", {})
    market_map = cfg.get("market_map", {"HK": "HKStock", "US": "USStock", "CN": "CNStock"})
    env_name = (env_override or od.get("trd_env", "SIMULATE")).upper()
    trd_env = TrdEnv.REAL if env_name == "REAL" else TrdEnv.SIMULATE

    market_enum = {"HK": TrdMarket.HK, "US": TrdMarket.US, "CN": TrdMarket.CN}
    firm = getattr(SecurityFirm, od.get("security_firm", "FUTUSECURITIES"),
                   SecurityFirm.FUTUSECURITIES)

    positions: list[dict] = []
    errors: list[dict] = []

    for mkt_name in od.get("markets", ["HK", "US"]):
        mkt = market_enum.get(mkt_name.upper())
        if mkt is None:
            errors.append({"market": mkt_name, "reason": "未知市场"})
            continue
        ctx = None
        try:
            ctx = OpenSecTradeContext(
                filter_trdmarket=mkt,
                host=od.get("host", "127.0.0.1"),
                port=int(od.get("port", 11111)),
                security_firm=firm,
            )
            ret, data = ctx.position_list_query(trd_env=trd_env)
            if ret != RET_OK:
                errors.append({"market": mkt_name, "reason": str(data)[:120]})
                continue
            for _, row in data.iterrows():
                code = row["code"]
                market, symbol = to_qd_symbol(code, market_map)
                positions.append({
                    "market": market,
                    "symbol": symbol,
                    "name": row.get("stock_name", ""),
                    "qty": float(row.get("qty") or 0),
                    "cost": row.get("cost_price"),
                    "pl_ratio": row.get("pl_ratio"),
                    "futu_code": code,
                })
        except Exception as e:  # 单个市场失败不影响其它市场
            errors.append({"market": mkt_name, "reason": str(e)[:120]})
        finally:
            if ctx is not None:
                try:
                    ctx.close()
                except Exception:
                    pass

    return {
        "as_of": datetime.now().isoformat(timespec="seconds"),
        "trd_env": env_name,
        "positions": positions,
        "errors": errors,
    }


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="读取富途 OpenD 持仓(只读)")
    p.add_argument("--config", default=str(DEFAULT_CONFIG))
    p.add_argument("--env", choices=["SIMULATE", "REAL"], default=None,
                   help="覆盖配置里的交易环境")
    p.add_argument("--quiet", action="store_true", help="只输出 JSON")
    args = p.parse_args(argv)

    cfg = load_config(Path(args.config))
    result = fetch_positions(cfg, args.env)

    if not args.quiet:
        n = len(result["positions"])
        env = result["trd_env"]
        flag = "" if env == "SIMULATE" else "  ⚠️ 真实账户"
        print(f"[{env}]{flag} 持仓 {n} 个", file=sys.stderr)
        if result["errors"]:
            print(f"  {len(result['errors'])} 个市场查询失败", file=sys.stderr)

    print(json.dumps(result, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
