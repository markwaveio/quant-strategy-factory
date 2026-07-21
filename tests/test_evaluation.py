"""评估层测试:三个 AI 角色的判断 + 委员会聚合逻辑。"""

from qsf.core.config import EvaluationConfig
from qsf.core.schema import (
    BacktestMetrics,
    ReviewRole,
    StrategyFamily,
    StrategyProposal,
    Verdict,
)
from qsf.evaluation.judge import MockLLMJudge, ReviewContext


def _proposal():
    return StrategyProposal(
        id="s1", family=StrategyFamily.TREND, symbol="DEMO.A",
        rationale="test", params={"fast": 10, "slow": 30},
    )


def _metrics(**kw):
    base = dict(
        strategy_id="s1", total_return=0.2, annual_return=0.15,
        max_drawdown=-0.1, sharpe=1.5, win_rate=0.6, n_trades=20,
    )
    base.update(kw)
    return BacktestMetrics(**base)


def test_risk_officer_rejects_over_limit_drawdown():
    judge = MockLLMJudge()
    ctx = ReviewContext(
        proposal=_proposal(),
        metrics=_metrics(max_drawdown=-0.4),  # 超过 25% 红线
        market_features={},
        similar_failures=0,
        max_drawdown_limit=0.25,
    )
    op = judge.review(ReviewRole.RISK_OFFICER, ctx)
    assert op.verdict == Verdict.REJECT
    assert op.concerns  # 应给出理由


def test_skeptic_flags_too_few_trades():
    judge = MockLLMJudge()
    ctx = ReviewContext(
        proposal=_proposal(),
        metrics=_metrics(n_trades=2),  # 样本太少
        market_features={},
        similar_failures=0,
        max_drawdown_limit=0.25,
    )
    op = judge.review(ReviewRole.SKEPTIC, ctx)
    assert op.verdict == Verdict.REVISE
    assert any("过拟合" in c for c in op.concerns)


def test_board_risk_officer_has_veto():
    from datetime import datetime

    from qsf.core.schema import MarketSnapshot
    from qsf.evaluation.committee import EvaluationBoard

    board = EvaluationBoard(EvaluationConfig())
    snap = MarketSnapshot(as_of=datetime.now())
    # 回撤爆表:即便收益不错,风险官也应一票否决
    result = board.evaluate(_proposal(), _metrics(max_drawdown=-0.5), snap, [])
    assert result.final_verdict == Verdict.REJECT


def test_board_approves_good_strategy():
    from datetime import datetime

    from qsf.core.schema import MarketSnapshot
    from qsf.evaluation.committee import EvaluationBoard

    board = EvaluationBoard(EvaluationConfig())
    snap = MarketSnapshot(as_of=datetime.now())
    result = board.evaluate(_proposal(), _metrics(), snap, [])
    # 指标健康、无历史失败 → 应通过
    assert result.final_verdict == Verdict.APPROVE
    assert result.aggregate_score > 0.6
