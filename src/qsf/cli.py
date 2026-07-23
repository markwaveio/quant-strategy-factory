"""命令行入口:一条命令跑通整条 8 层流水线。

用法:
    qsf run                      # 用默认 mock 配置跑一遍,打印决策报告
    qsf run --config configs/default.yaml
    qsf run --report reports/latest.md   # 额外把 Markdown 落盘
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from qsf.core.config import Config
from qsf.core.pipeline import Pipeline
from qsf.research.reporter import render_research_markdown, strategy_payload_json
from qsf.review.report import render_markdown


def _cmd_run(args: argparse.Namespace) -> int:
    config = Config.from_yaml(args.config) if args.config else Config()

    pipeline = Pipeline(config)
    result = pipeline.run()

    assert result.report is not None
    md = render_markdown(result.report, result.review, result.scan_candidates)
    print(md)

    if args.report:
        out = Path(args.report)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(md, encoding="utf-8")
        print(f"\n[已写入报告] {out}", file=sys.stderr)

    if args.research_report:
        assert result.research_report is not None
        out = Path(args.research_report)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(render_research_markdown(result.research_report), encoding="utf-8")
        print(f"\n[已写入研究证据包] {out}", file=sys.stderr)

    if args.strategies:
        assert result.research_report is not None
        out = Path(args.strategies)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(strategy_payload_json(result.research_report), encoding="utf-8")
        print(f"\n[已写入策略候选 JSON] {out}", file=sys.stderr)

    # 简要统计到 stderr,方便脚本消费
    summary = Pipeline.summarize(result)
    print(f"\n[统计] {summary}", file=sys.stderr)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="qsf", description="量化策略工厂:数据→洞察→策略→评估→回测→决策→复盘"
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_run = sub.add_parser("run", help="运行一次完整流水线")
    p_run.add_argument("--config", help="YAML 配置文件路径", default=None)
    p_run.add_argument("--report", help="把 Markdown 报告写入该路径", default=None)
    p_run.add_argument(
        "--research-report",
        help="把策略研究证据包 Markdown 写入该路径",
        default=None,
    )
    p_run.add_argument(
        "--strategies",
        help="把策略候选证据 JSON 写入该路径,供模拟交易或外部回测读取",
        default=None,
    )
    p_run.set_defaults(func=_cmd_run)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
