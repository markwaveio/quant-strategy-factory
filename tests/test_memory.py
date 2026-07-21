"""记忆层测试:写入、读取、成败判定、检索。"""

from datetime import datetime

from qsf.core.schema import (
    BacktestMetrics,
    EvaluationResult,
    MarketSnapshot,
    Outcome,
    StrategyFamily,
    StrategyProposal,
    Verdict,
)
from qsf.memory.store import MemoryStore


def _proposal():
    return StrategyProposal(
        id="s1", family=StrategyFamily.TREND, symbol="DEMO.A", rationale="t"
    )


def _metrics(total_return=0.2, sharpe=1.5):
    return BacktestMetrics(
        strategy_id="s1", total_return=total_return, annual_return=0.1,
        max_drawdown=-0.05, sharpe=sharpe, win_rate=0.6, n_trades=10,
    )


def test_remember_and_read_back(tmp_path):
    store = MemoryStore(tmp_path / "mem.jsonl")
    snap = MarketSnapshot(as_of=datetime.now())
    ev = EvaluationResult(strategy_id="s1", opinions=[], final_verdict=Verdict.APPROVE)
    rec = store.remember(_proposal(), _metrics(), snap, ev)
    assert rec.outcome == Outcome.SUCCESS
    # 读回
    all_recs = store.all()
    assert len(all_recs) == 1
    assert all_recs[0].strategy_id == "s1"


def test_failure_outcome(tmp_path):
    store = MemoryStore(tmp_path / "mem.jsonl")
    snap = MarketSnapshot(as_of=datetime.now())
    ev = EvaluationResult(strategy_id="s1", opinions=[], final_verdict=Verdict.REJECT)
    rec = store.remember(_proposal(), _metrics(total_return=-0.1), snap, ev)
    assert rec.outcome == Outcome.FAILURE


def test_query_and_stats(tmp_path):
    store = MemoryStore(tmp_path / "mem.jsonl")
    snap = MarketSnapshot(as_of=datetime.now())
    ev_ok = EvaluationResult(strategy_id="s1", opinions=[], final_verdict=Verdict.APPROVE)
    ev_bad = EvaluationResult(strategy_id="s1", opinions=[], final_verdict=Verdict.REJECT)
    store.remember(_proposal(), _metrics(), snap, ev_ok)
    store.remember(_proposal(), _metrics(total_return=-0.2), snap, ev_bad)

    failures = store.query(family=StrategyFamily.TREND, outcome=Outcome.FAILURE)
    assert len(failures) == 1
    stats = store.stats()
    assert stats["success"] == 1 and stats["failure"] == 1
