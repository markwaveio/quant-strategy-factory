import yaml
from scripts.curate_reports import main as curate_main
from scripts.import_futu_review import main

from qsf.core.schema import MemoryRecord, Outcome


def test_import_futu_review_writes_markdown_and_memory(tmp_path):
    review = {
        "date": "2026-07-24",
        "strategy_id": "trend:01177:abc123",
        "symbol": "01177",
        "family": "trend",
        "source_file": "reports/futu-oos/futu-01177-trend-fast8-slow20.py",
        "params": {"fast": 8, "slow": 20},
        "futu_backtest": {
            "start": "2024-03-15",
            "end": "2026-07-24",
            "initial_cash": 100000,
            "total_return": 0.31,
            "annual_return": 0.13,
            "max_drawdown": -0.12,
            "sharpe": 1.4,
            "trades": 11,
            "benchmark_return": 0.2,
            "verdict": "pass",
        },
        "notes": ["截图由 agent 识别"],
    }
    src = tmp_path / "review.yaml"
    reports = tmp_path / "reports"
    memory = tmp_path / "memory.jsonl"
    src.write_text(yaml.safe_dump(review, allow_unicode=True), encoding="utf-8")

    code = main([str(src), "--reports-dir", str(reports), "--memory-path", str(memory)])

    assert code == 0
    review_files = list((reports / "futu-reviews").glob("*.md"))
    assert len(review_files) == 1
    text = review_files[0].read_text(encoding="utf-8")
    assert "富途回测复盘 · 01177 · 2026-07-24" in text
    assert "收益:31.0%" in text
    record = MemoryRecord.model_validate_json(memory.read_text(encoding="utf-8").strip())
    assert record.strategy_id == "trend:01177:abc123"
    assert record.outcome == Outcome.SUCCESS
    assert record.provenance.engine == "futu-ui-backtest"


def test_curate_reports_shows_latest_futu_review(tmp_path):
    reports = tmp_path / "reports"
    (reports / "futu-reviews").mkdir(parents=True)
    (reports / "futu-reviews" / "2026-07-24-01177-trend.md").write_text(
        "review", encoding="utf-8"
    )

    code = curate_main(["--reports-dir", str(reports)])

    assert code == 0
    index = (reports / "README.md").read_text(encoding="utf-8")
    assert "最新富途复盘:futu-reviews/2026-07-24-01177-trend.md" in index
    assert "富途复盘:1" in index
