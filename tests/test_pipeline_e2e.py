"""端到端测试:整条 8 层流水线离线跑通,产物结构完整。"""

from qsf.core.config import Config, DataConfig
from qsf.core.pipeline import Pipeline
from qsf.review.report import render_markdown


def _config(tmp_path):
    cfg = Config()
    cfg.data = DataConfig(symbols=["DEMO.A", "DEMO.B", "DEMO.C"], lookback_days=200, seed=42)
    cfg.memory_path = str(tmp_path / "mem.jsonl")
    return cfg


def test_full_pipeline_runs(tmp_path):
    pipeline = Pipeline(_config(tmp_path))
    result = pipeline.run()

    # 每一层都应有产物
    assert result.snapshot.symbols == ["DEMO.A", "DEMO.B", "DEMO.C"]
    assert len(result.proposals) >= 3            # 至少每标的一个
    assert len(result.evaluations) == len(result.proposals)
    assert len(result.backtests) == len(result.proposals)
    assert result.report is not None
    assert result.review is not None

    # 决策覆盖所有标的
    decided_symbols = {d.symbol for d in result.report.decisions}
    assert decided_symbols == {"DEMO.A", "DEMO.B", "DEMO.C"}

    # 复盘统计自洽
    assert result.review.n_proposals == len(result.proposals)
    assert result.review.n_approved + result.review.n_rejected <= result.review.n_proposals


def test_memory_persists_across_runs(tmp_path):
    cfg = _config(tmp_path)
    Pipeline(cfg).run()
    n_after_first = len(Pipeline(cfg).memory_store.all())
    Pipeline(cfg).run()
    n_after_second = len(Pipeline(cfg).memory_store.all())
    # 第二轮应在第一轮基础上继续累积经验
    assert n_after_second > n_after_first


def test_report_renders_markdown(tmp_path):
    result = Pipeline(_config(tmp_path)).run()
    md = render_markdown(result.report, result.review)
    assert "# 量化决策报告" in md
    assert "决策建议" in md
    # 每个标的都应出现在报告里
    for sym in ("DEMO.A", "DEMO.B", "DEMO.C"):
        assert sym in md


def test_summary_counts(tmp_path):
    result = Pipeline(_config(tmp_path)).run()
    summary = Pipeline.summarize(result)
    assert summary["proposals"] == len(result.proposals)
    assert summary["approved"] + summary["rejected"] <= summary["proposals"]


def test_opend_hk_daily_and_strict_configs_load():
    daily = Config.from_yaml("configs/opend-hk-daily-research.yaml")
    strict = Config.from_yaml("configs/opend-hk-strict-opportunity.yaml")

    assert daily.data.mode == "opend"
    assert daily.data.lookback_days == 500
    assert daily.scan.enabled is False
    assert daily.strategy.param_search is True
    assert daily.strategy.min_trades == 10

    assert strict.data.mode == "opend"
    assert strict.data.lookback_days == 500
    assert strict.scan.enabled is True
    assert strict.scan.min_score == 0.35
    assert strict.strategy.param_search is True
