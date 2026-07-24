#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import shutil
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path


@dataclass
class Candidate:
    symbol: str
    family: str
    status: str
    action: str
    params: dict
    strategy_id: str
    score: float
    opportunity: int
    recommendation: int
    oos_excess: float | None
    max_drawdown: float | None
    n_trades: int | None
    source: Path
    futu_file: Path | None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="整理 QSF reports 目录")
    parser.add_argument("--reports-dir", default="reports")
    parser.add_argument("--output", default=None)
    parser.add_argument("--archive-stale", action="store_true")
    parser.add_argument("--keep-days", type=int, default=14)
    args = parser.parse_args(argv)

    reports_dir = Path(args.reports_dir)
    if not reports_dir.exists():
        print(f"❌ reports 目录不存在:{reports_dir}", file=sys.stderr)
        return 1

    candidates = _load_candidates(reports_dir)
    promoted = [c for c in candidates if c.status == "promote_to_sim"]
    promoted.sort(
        key=lambda c: (
            c.recommendation,
            c.opportunity,
            c.oos_excess if c.oos_excess is not None else -999.0,
            c.score,
        ),
        reverse=True,
    )

    archived: list[Path] = []
    if args.archive_stale:
        archived = _archive_stale(reports_dir, promoted, args.keep_days)

    output = Path(args.output) if args.output else reports_dir / "README.md"
    output.write_text(_render_index(reports_dir, promoted, candidates, archived), encoding="utf-8")
    print(f"✅ 已生成报告索引:{output}", file=sys.stderr)
    if archived:
        print(f"✅ 已归档 {len(archived)} 个旧/中间产物", file=sys.stderr)
    return 0


def _load_candidates(reports_dir: Path) -> list[Candidate]:
    futu_files = list(reports_dir.glob("**/futu-*.py"))
    out: list[Candidate] = []
    for path in sorted(reports_dir.glob("**/strategies*.json")):
        if "_archive" in path.parts:
            continue
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        for item in payload.get("strategies", []):
            metrics = item.get("metrics") or {}
            params = item.get("params") or {}
            symbol = str(item.get("symbol", "-"))
            family = str(item.get("family", "-"))
            status = str(item.get("status", "-"))
            action = str(item.get("action", "-"))
            score = float(item.get("aggregate_score") or 0.0)
            oos_excess = _num(metrics.get("oos_excess"))
            max_drawdown = _num(metrics.get("max_drawdown"))
            n_trades = _int(metrics.get("n_trades"))
            candidate = Candidate(
                symbol=symbol,
                family=family,
                status=status,
                action=action,
                params=params,
                strategy_id=str(item.get("strategy_id", "-")),
                score=score,
                opportunity=_opportunity_score(item, metrics),
                recommendation=_recommendation_score(item, metrics),
                oos_excess=oos_excess,
                max_drawdown=max_drawdown,
                n_trades=n_trades,
                source=path,
                futu_file=_match_futu_file(futu_files, symbol, family, params),
            )
            out.append(candidate)
    return out


def _opportunity_score(item: dict, metrics: dict) -> int:
    score = 0
    if item.get("status") == "promote_to_sim":
        score += 35
    if item.get("action") in ("buy", "sell"):
        score += 15
    oos = _num(metrics.get("oos_excess"))
    if oos is not None:
        score += max(0, min(25, round(oos * 100)))
    trades = _int(metrics.get("n_trades")) or 0
    if trades >= 10:
        score += 10
    drawdown = abs(_num(metrics.get("max_drawdown")) or 0.0)
    if drawdown <= 0.15:
        score += 10
    elif drawdown <= 0.25:
        score += 5
    if item.get("triggered_by"):
        score += 5
    return max(0, min(100, score))


def _recommendation_score(item: dict, metrics: dict) -> int:
    score = _opportunity_score(item, metrics)
    final_verdict = item.get("final_verdict")
    if final_verdict == "approve":
        score += 10
    elif final_verdict == "reject":
        score -= 25
    if _num(metrics.get("oos_excess")) is not None and _num(metrics.get("oos_excess")) <= 0:
        score -= 20
    if abs(_num(metrics.get("max_drawdown")) or 0.0) > 0.25:
        score -= 20
    if (_int(metrics.get("n_trades")) or 0) < 10:
        score -= 10
    return max(0, min(100, score))


def _match_futu_file(files: list[Path], symbol: str, family: str, params: dict) -> Path | None:
    fast = params.get("fast")
    slow = params.get("slow")
    if family != "trend" or fast is None or slow is None:
        return None
    needles = [symbol, f"fast{int(fast)}", f"slow{int(slow)}"]
    for file in files:
        name = file.name
        if all(needle in name for needle in needles):
            return file
    return None


def _archive_stale(reports_dir: Path, promoted: list[Candidate], keep_days: int) -> list[Path]:
    keep = {reports_dir / "README.md"}
    keep.update(c.source for c in promoted)
    keep.update(c.futu_file for c in promoted if c.futu_file is not None)
    keep.update(p for p in reports_dir.glob("factory-oos/strategy-cards*.md"))
    keep.update(p for p in reports_dir.glob("futu-oos/*.py"))

    cutoff = datetime.now() - timedelta(days=keep_days)
    archive_dir = reports_dir / "_archive" / datetime.now().strftime("%Y%m%d-%H%M%S")
    archived: list[Path] = []
    for path in sorted(reports_dir.glob("**/*")):
        if not path.is_file() or "_archive" in path.parts or path in keep:
            continue
        if _is_stale(path, cutoff):
            target = archive_dir / path.relative_to(reports_dir)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(path), str(target))
            archived.append(path)
    return archived


def _is_stale(path: Path, cutoff: datetime) -> bool:
    if "-oos" not in path.as_posix() and path.suffix in {".md", ".json", ".py"}:
        return True
    return datetime.fromtimestamp(path.stat().st_mtime) < cutoff


def _render_index(
    reports_dir: Path,
    promoted: list[Candidate],
    all_candidates: list[Candidate],
    archived: list[Path],
) -> str:
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    lines = [
        f"# QSF 报告索引 · {now}",
        "",
        "> 只看这页。旧口径和中间产物已在下方标注,避免从文件夹里盲找。",
        "",
        "## 今日先看",
        "",
    ]
    if promoted:
        best = promoted[0]
        lines.append(f"- 最优候选:{best.symbol} `{_params(best.params)}`")
        lines.append(f"- 推荐指数:{best.recommendation}/100")
        lines.append(f"- 机会指数:{best.opportunity}/100")
        lines.append(f"- OOS超额:{_pct(best.oos_excess)}")
        lines.append(f"- 最大回撤:{_pct(best.max_drawdown)}")
        if best.futu_file:
            lines.append(f"- 富途代码:{best.futu_file.relative_to(reports_dir)}")
    else:
        lines.append("- 今天没有进入模拟验证的候选。")
    lines.append("")

    lines.extend([
        "## 候选排行",
        "",
        "| 排名 | 标的 | 策略 | 推荐 | 机会 | OOS超额 | 回撤 | 交易 | 富途代码 | 来源 |",
        "| ---: | --- | --- | ---: | ---: | ---: | ---: | ---: | --- | --- |",
    ])
    for i, c in enumerate(promoted[:10], start=1):
        lines.append(
            f"| {i} | {c.symbol} | {c.family} `{_params(c.params)}` | "
            f"{c.recommendation} | {c.opportunity} | {_pct(c.oos_excess)} | "
            f"{_pct(c.max_drawdown)} | {c.n_trades or '-'} | "
            f"{_rel(c.futu_file, reports_dir)} | {_rel(c.source, reports_dir)} |"
        )
    if not promoted:
        lines.append("| - | - | - | - | - | - | - | - | - | - |")
    lines.append("")

    lines.extend([
        "## 文件规则",
        "",
        "- `reports/README.md`:每天先看这一页。",
        "- `factory-oos/strategy-cards*.md`:人工阅读版结论。",
        "- `futu-oos/`:拿去富途复验的代码。",
        "- `_archive/`:旧口径、过期或中间产物;默认不要看。",
        "",
        "## 统计",
        "",
        f"- 候选总数:{len(all_candidates)}",
        f"- 进入模拟验证:{len(promoted)}",
        f"- 本次归档:{len(archived)}",
    ])
    return "\n".join(lines) + "\n"


def _params(params: dict) -> str:
    return ", ".join(f"{k}={v:g}" for k, v in params.items()) if params else "-"


def _pct(value: float | None) -> str:
    return "-" if value is None else f"{value:+.1%}"


def _rel(path: Path | None, root: Path) -> str:
    return "-" if path is None else str(path.relative_to(root))


def _num(value) -> float | None:
    return float(value) if isinstance(value, int | float) else None


def _int(value) -> int | None:
    return int(value) if isinstance(value, int | float) else None


if __name__ == "__main__":
    raise SystemExit(main())
