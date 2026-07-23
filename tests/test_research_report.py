"""研究证据包:确保策略发现产物可归档、可导出。"""

from __future__ import annotations

import json

from qsf.cli import main
from qsf.core.config import Config, DataConfig
from qsf.core.pipeline import Pipeline
from qsf.core.schema import ResearchStatus
from qsf.research.reporter import render_research_markdown


def _config(tmp_path):
    cfg = Config()
    cfg.data = DataConfig(symbols=["DEMO.A", "DEMO.B", "DEMO.C"], lookback_days=200, seed=42)
    cfg.memory_path = str(tmp_path / "mem.jsonl")
    return cfg


def test_pipeline_builds_research_report(tmp_path):
    result = Pipeline(_config(tmp_path)).run()

    assert result.research_report is not None
    assert len(result.research_report.cards) == len(result.proposals)
    assert result.research_report.summary
    assert {c.status for c in result.research_report.cards} <= set(ResearchStatus)


def test_research_markdown_renders_strategy_evidence(tmp_path):
    result = Pipeline(_config(tmp_path)).run()
    md = render_research_markdown(result.research_report)

    assert "# 策略研究证据包" in md
    assert "候选策略" in md
    assert "下一步" in md
    assert "DEMO.A" in md


def test_cli_exports_reports_and_strategy_json(tmp_path):
    report = tmp_path / "decision.md"
    research = tmp_path / "research.md"
    strategies = tmp_path / "strategies.json"

    code = main([
        "run",
        "--report", str(report),
        "--research-report", str(research),
        "--strategies", str(strategies),
    ])

    assert code == 0
    assert "# 量化决策报告" in report.read_text(encoding="utf-8")
    assert "# 策略研究证据包" in research.read_text(encoding="utf-8")
    payload = json.loads(strategies.read_text(encoding="utf-8"))
    assert payload["strategies"]
    card = payload["strategies"][0]
    assert {"strategy_id", "symbol", "family", "status", "params", "metrics"} <= set(card)
    assert "equity_curve" not in card["metrics"]
