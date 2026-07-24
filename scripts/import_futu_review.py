#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

import yaml

from qsf.core.schema import (
    BacktestMetrics,
    BacktestProvenance,
    MemoryRecord,
    Outcome,
    StrategyFamily,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="导入富途回测截图复盘结果")
    parser.add_argument("review_file", help="agent 从富途截图整理出的 YAML/JSON")
    parser.add_argument("--reports-dir", default="reports")
    parser.add_argument("--memory-path", default="data_store/memory/memory.jsonl")
    parser.add_argument("--no-memory", action="store_true")
    args = parser.parse_args(argv)

    review_path = Path(args.review_file)
    if not review_path.exists():
        print(f"❌ 复盘文件不存在:{review_path}", file=sys.stderr)
        return 1

    payload = _load_payload(review_path)
    errors = _validate(payload)
    if errors:
        for error in errors:
            print(f"❌ {error}", file=sys.stderr)
        return 1

    reports_dir = Path(args.reports_dir)
    out_dir = reports_dir / "futu-reviews"
    out_dir.mkdir(parents=True, exist_ok=True)
    review_id = _review_id(payload)
    report_path = out_dir / f"{review_id}.md"
    report_path.write_text(_render_markdown(payload, review_path), encoding="utf-8")

    if not args.no_memory:
        memory_path = Path(args.memory_path)
        memory_path.parent.mkdir(parents=True, exist_ok=True)
        record = _memory_record(payload)
        with memory_path.open("a", encoding="utf-8") as f:
            f.write(record.model_dump_json() + "\n")

    print(f"✅ 已写入富途复盘:{report_path}", file=sys.stderr)
    if not args.no_memory:
        print(f"✅ 已写入记忆层:{args.memory_path}", file=sys.stderr)
    return 0


def _load_payload(path: Path) -> dict:
    text = path.read_text(encoding="utf-8")
    if path.suffix.lower() == ".json":
        return json.loads(text)
    data = yaml.safe_load(text) or {}
    if not isinstance(data, dict):
        raise ValueError("复盘文件必须是对象")
    return data


def _validate(payload: dict) -> list[str]:
    required = ["date", "strategy_id", "symbol", "family", "params", "futu_backtest"]
    errors = [f"缺少字段:{key}" for key in required if key not in payload]
    bt = payload.get("futu_backtest") or {}
    for key in ("start", "end", "total_return", "max_drawdown"):
        if key not in bt:
            errors.append(f"缺少字段:futu_backtest.{key}")
    return errors


def _review_id(payload: dict) -> str:
    date = str(payload["date"]).replace("/", "-")
    symbol = str(payload["symbol"])
    sid = str(payload["strategy_id"]).replace(":", "-")
    return f"{date}-{symbol}-{sid}"


def _memory_record(payload: dict) -> MemoryRecord:
    bt = payload["futu_backtest"]
    outcome = _outcome(payload)
    metrics = BacktestMetrics(
        strategy_id=str(payload["strategy_id"]),
        total_return=float(bt["total_return"]),
        annual_return=float(bt.get("annual_return", bt["total_return"])),
        max_drawdown=float(bt["max_drawdown"]),
        sharpe=float(bt.get("sharpe", 0.0)),
        win_rate=float(bt.get("win_rate", 0.0)),
        n_trades=int(bt.get("trades", bt.get("n_trades", 0))),
        benchmark_return=_maybe_float(bt.get("benchmark_return")),
        oos_return=_maybe_float(bt.get("oos_return")),
        oos_benchmark_return=_maybe_float(bt.get("oos_benchmark_return")),
    )
    provenance = BacktestProvenance(
        engine="futu-ui-backtest",
        data_source="futu",
        start_date=str(bt["start"]),
        end_date=str(bt["end"]),
        oos_enabled=False,
    )
    return MemoryRecord(
        strategy_id=str(payload["strategy_id"]),
        family=StrategyFamily(str(payload["family"])),
        symbol=str(payload["symbol"]),
        created_at=datetime.now(),
        outcome=outcome,
        metrics=metrics,
        lesson=_lesson(payload, outcome),
        params=dict(payload.get("params") or {}),
        provenance=provenance,
        strategy_version=str(payload.get("strategy_version", "futu-review-v1")),
        regime=str(payload.get("regime", "")),
    )


def _outcome(payload: dict) -> Outcome:
    bt = payload["futu_backtest"]
    verdict = str(bt.get("verdict", "")).lower()
    if verdict in {"pass", "success", "approve"}:
        return Outcome.SUCCESS
    if verdict in {"fail", "failure", "reject"}:
        return Outcome.FAILURE
    total = float(bt["total_return"])
    drawdown = abs(float(bt["max_drawdown"]))
    benchmark = _maybe_float(bt.get("benchmark_return"))
    if total <= 0 or drawdown > 0.25:
        return Outcome.FAILURE
    if benchmark is not None and total <= benchmark:
        return Outcome.NEUTRAL
    return Outcome.SUCCESS


def _lesson(payload: dict, outcome: Outcome) -> str:
    bt = payload["futu_backtest"]
    prefix = "富途回测通过" if outcome == Outcome.SUCCESS else "富途回测未通过"
    if outcome == Outcome.NEUTRAL:
        prefix = "富途回测中性"
    return (
        f"{prefix}:{payload['family']} {payload['symbol']} "
        f"收益 {float(bt['total_return']):.1%},回撤 {float(bt['max_drawdown']):.1%}"
    )


def _render_markdown(payload: dict, source: Path) -> str:
    bt = payload["futu_backtest"]
    notes = payload.get("notes") or []
    lines = [
        f"# 富途回测复盘 · {payload['symbol']} · {payload['date']}",
        "",
        f"- 策略ID:{payload['strategy_id']}",
        f"- 策略族:{payload['family']}",
        f"- 参数:{_params(payload.get('params') or {})}",
        f"- 富途代码:{payload.get('source_file', '-')}",
        f"- 截图/录入来源:{source}",
        "",
        "## 富途结果",
        "",
        f"- 区间:{bt['start']} ~ {bt['end']}",
        f"- 初始资金:{bt.get('initial_cash', '-')}",
        f"- 收益:{float(bt['total_return']):.1%}",
        f"- 最大回撤:{float(bt['max_drawdown']):.1%}",
        f"- 夏普:{bt.get('sharpe', '-')}",
        f"- 交易次数:{bt.get('trades', bt.get('n_trades', '-'))}",
        f"- 结论:{bt.get('verdict', '-')}",
        "",
        "## 复盘总结",
        "",
        f"- {_lesson(payload, _outcome(payload))}",
    ]
    for note in notes:
        lines.append(f"- {note}")
    lines.append("")
    lines.append("---")
    lines.append("")
    lines.append("_富途截图由 agent 识别后录入;本文件仅供研究复盘,不构成投资建议。_")
    return "\n".join(lines) + "\n"


def _params(params: dict) -> str:
    return ", ".join(f"{k}={v:g}" for k, v in params.items()) if params else "-"


def _maybe_float(value) -> float | None:
    return float(value) if isinstance(value, int | float) else None


if __name__ == "__main__":
    raise SystemExit(main())
