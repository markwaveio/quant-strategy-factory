from qsf.core.config import Config, DataConfig, ScanConfig
from qsf.core.pipeline import Pipeline
from qsf.data.provider import MockDataProvider
from qsf.insight.engine import InsightEngine
from qsf.review.report import render_markdown
from qsf.scan import DailyScanner


def test_daily_scanner_ranks_candidates():
    cfg = DataConfig(symbols=["DEMO.A", "DEMO.B", "DEMO.C"], lookback_days=200, seed=42)
    snapshot = MockDataProvider(cfg).build_snapshot()
    insights = InsightEngine().analyze(snapshot)

    candidates = DailyScanner(ScanConfig(enabled=True, max_candidates=2, min_score=0.1)).scan(
        snapshot, insights
    )

    assert 0 < len(candidates) <= 2
    assert candidates == sorted(candidates, key=lambda c: c.score, reverse=True)
    assert all(c.reasons for c in candidates)
    assert all(c.recommended_families for c in candidates)


def test_pipeline_scan_first_limits_strategy_universe(tmp_path):
    cfg = Config()
    cfg.data = DataConfig(symbols=["DEMO.A", "DEMO.B", "DEMO.C"], lookback_days=200, seed=42)
    cfg.scan = ScanConfig(enabled=True, max_candidates=1, min_score=0.1)
    cfg.memory_path = str(tmp_path / "mem.jsonl")

    result = Pipeline(cfg).run()

    candidate_symbols = {c.symbol for c in result.scan_candidates}
    proposal_symbols = {p.symbol for p in result.proposals}
    decision_symbols = {d.symbol for d in result.report.decisions}

    assert len(candidate_symbols) == 1
    assert proposal_symbols == candidate_symbols
    assert decision_symbols == candidate_symbols


def test_scan_candidates_render_in_report(tmp_path):
    cfg = Config()
    cfg.data = DataConfig(symbols=["DEMO.A", "DEMO.B", "DEMO.C"], lookback_days=200, seed=42)
    cfg.scan = ScanConfig(enabled=True, max_candidates=2, min_score=0.1)
    cfg.memory_path = str(tmp_path / "mem.jsonl")

    result = Pipeline(cfg).run()
    md = render_markdown(result.report, result.review, result.scan_candidates)

    assert "每日扫描候选" in md
    for candidate in result.scan_candidates:
        assert candidate.symbol in md
