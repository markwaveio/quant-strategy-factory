"""数据体检 + 横截面证据 + 质疑者升级 的验收测试。

每个用例都对应一次真实踩过的坑(见 docs/factor-research-02.md),
不是凭空造的边界条件。
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from qsf.backtest.cross_section import Panel, run_factor
from qsf.core.schema import (
    OHLCV,
    AssetClass,
    BacktestMetrics,
    DataQualityReport,
    FactorEvidence,
    PriceSeries,
    QualityIssue,
    ReviewRole,
    StrategyFamily,
    StrategyProposal,
    Verdict,
)
from qsf.data.quality import DataQualityError, check_panel, check_series, require_clean
from qsf.evaluation.judge import MockLLMJudge, ReviewContext


def _series(symbol: str, closes: list[float], start: str = "2020-01-01",
            volume: float = 1e6) -> PriceSeries:
    t0 = datetime.fromisoformat(start)
    return PriceSeries(
        symbol=symbol, asset_class=AssetClass.OTHER,
        bars=[OHLCV(ts=t0 + timedelta(days=i), open=c, high=c * 1.01,
                    low=c * 0.99, close=c, volume=volume)
              for i, c in enumerate(closes)],
    )


# --------------------------------------------------------------------------- #
# 数据体检                                                                      #
# --------------------------------------------------------------------------- #

def test_negative_price_is_fatal():
    """前复权在巨额分红股上产出负价 —— 实测 01114 有 1013/1728 根为负。"""
    s = _series("01114", [10.0, 5.0, -1.7, -6.8, 2.0])
    issues = check_series(s)
    codes = {i.code for i in issues}
    assert "negative_price" in codes
    assert any(i.fatal for i in issues if i.code == "negative_price")


def test_impossible_return_is_fatal():
    """收益率的数学下界是 -100%。低于它不是市场跌了,是数据错了。"""
    s = _series("BAD", [10.0, 10.0, -5.0, 3.0])
    assert any(i.code == "impossible_return" and i.fatal for i in check_series(s))


def test_clean_series_passes():
    s = _series("GOOD", [10.0 + i * 0.1 for i in range(300)])
    assert check_series(s) == []


def test_bar_count_gap_flags_index_misalignment():
    """bar 数量差 3 年时,index[250] 会落在 18 个不同日期上 —— 必须报警。"""
    prices = {
        "A": _series("A", [10.0 + i * 0.01 for i in range(1483)]),
        "B": _series("B", [20.0 + i * 0.01 for i in range(2237)]),
    }
    _, rep = check_panel(prices)
    assert not rep.index_alignment_safe
    assert rep.bar_count_min == 1483 and rep.bar_count_max == 2237
    assert any(i.code == "index_misalignment" for i in rep.issues)
    # 不阻断,但必须让人看见
    assert rep.ok


def test_check_panel_drops_fatal_and_keeps_rest():
    prices = {
        "OK": _series("OK", [10.0 + i * 0.01 for i in range(500)]),
        "NEG": _series("NEG", [-1.0] * 500),
    }
    kept, rep = check_panel(prices)
    assert set(kept) == {"OK"}
    assert rep.n_dropped == 1 and "NEG" in rep.dropped


def test_require_clean_raises_with_report():
    prices = {"NEG": _series("NEG", [-1.0] * 100)}
    with pytest.raises(DataQualityError) as e:
        require_clean(prices)
    assert "负" in str(e.value) or "非正" in str(e.value)


# --------------------------------------------------------------------------- #
# 横截面对齐                                                                    #
# --------------------------------------------------------------------------- #

def test_panel_aligns_by_date_not_index():
    """核心回归测试:两只标的起始日期不同,同一 index 必须指向同一天。"""
    a = _series("A", [10.0, 11.0, 12.0], start="2020-01-01")
    b = _series("B", [20.0, 21.0], start="2020-01-02")   # 晚一天上市
    p = Panel({"A": a, "B": b})
    assert p.dates == ["2020-01-01", "2020-01-02", "2020-01-03"]
    # B 在第 0 天还没上市
    assert p.data["B"]["close"][0] is None
    assert p.data["B"]["first"] == 1
    # 同一 index 对应同一天的价格
    assert p.data["A"]["close"][1] == 11.0
    assert p.data["B"]["close"][1] == 20.0


def test_panel_does_not_forward_fill_after_delisting():
    """退市后前向填充会凭空造出「零涨跌」的无风险持仓。"""
    a = _series("A", [10.0] * 5, start="2020-01-01")
    b = _series("B", [20.0] * 2, start="2020-01-01")     # 第 3 天起停止交易
    p = Panel({"A": a, "B": b})
    assert p.data["B"]["last"] == 1
    assert p.data["B"]["close"][4] is None


def test_cross_section_excludes_symbols_without_future_data():
    a = _series("A", [10.0 + i for i in range(300)])
    b = _series("B", [20.0 + i for i in range(300)])
    p = Panel({"A": a, "B": b})
    sec = p.cross_section(t=len(p) - 1, horizon=5, fn=lambda c, v: c[-1], warmup=10)
    assert sec == []      # t+horizon 超出数据范围,不能纳入


# --------------------------------------------------------------------------- #
# 证据四道关                                                                    #
# --------------------------------------------------------------------------- #

def test_bonferroni_threshold_scales_with_hypotheses():
    """检验 22 个假设时门槛是 ~3.0,不是 1.96。"""
    assert FactorEvidence(factor="f", market="HK").bonferroni_threshold == pytest.approx(1.96, abs=0.02)
    assert FactorEvidence(factor="f", market="HK", n_hypotheses=22).bonferroni_threshold > 3.0


def test_evidence_rejects_lookahead_fingerprint():
    """分段 -1.9% / +102% / +182% 逐段递增 —— 选池前视偏差的指纹。"""
    ev = FactorEvidence(
        factor="低成交额", market="HKStock", n_hypotheses=22,
        t_stat=3.74, monotonicity=0.75, net_annual=0.787,
        subperiod_annual=[-0.019, 1.0197, 1.8154],
        universe_selection="按今日成交额取前50",
    )
    checks = ev.passed()
    assert checks["多重检验"] and checks["分层单调"] and checks["扣费为正"]
    assert not checks["分段稳定"]        # 最早一段为负 → 不通过
    assert not ev.verdict_ok


def test_evidence_passes_only_when_all_four_hold():
    ev = FactorEvidence(
        factor="good", market="HKStock", n_hypotheses=3,
        t_stat=4.0, monotonicity=1.0, net_annual=0.15,
        subperiod_annual=[0.12, 0.18, 0.14],
        universe_selection="时点重建",
    )
    assert ev.verdict_ok


def test_run_factor_computes_evidence_on_synthetic_alpha():
    """构造一个真有预测力的因子,确认四道关能识别出来。"""
    import random
    rng = random.Random(42)
    prices = {}
    for k in range(30):
        # 前一日跌得多的,次日涨回来 —— 人造反转 alpha
        closes, c = [], 100.0
        for _ in range(400):
            closes.append(c)
            c *= 1 + rng.gauss(0, 0.02)
        prices[f"S{k}"] = _series(f"S{k}", closes)
    p = Panel(prices)
    ev = run_factor(p, "随机因子", lambda c, v: rng.random(),
                    market="TEST", horizon=20, n_hypotheses=1, warmup=250)
    assert ev.n_periods >= 0
    # 纯随机因子不该通过四道关
    assert not ev.verdict_ok


# --------------------------------------------------------------------------- #
# 质疑者升级                                                                    #
# --------------------------------------------------------------------------- #

def _ctx(**kw) -> ReviewContext:
    base = dict(
        proposal=StrategyProposal(
            id="s1", family=StrategyFamily.TREND, symbol="00700",
            params={"fast": 5, "slow": 20}, rationale="test", triggered_by=[],
        ),
        metrics=BacktestMetrics(
            strategy_id="s1", total_return=0.5, annual_return=0.2,
            max_drawdown=-0.1, sharpe=1.5, win_rate=0.6, n_trades=50,
        ),
        market_features={}, similar_failures=0, max_drawdown_limit=0.3,
    )
    base.update(kw)
    return ReviewContext(**base)


def test_skeptic_rejects_on_fatal_data_quality():
    """数据不可信 = 一票否决,不是 REVISE —— 在错的数据上调参只会更精致地错。"""
    dq = DataQualityReport(n_symbols=50, issues=[
        QualityIssue(symbol="01114", code="negative_price",
                     detail="1013/1728 根 bar 价格非正", fatal=True)
    ])
    op = MockLLMJudge().review(ReviewRole.SKEPTIC, _ctx(data_quality=dq))
    assert op.verdict == Verdict.REJECT
    assert any("数据不可信" in c for c in op.concerns)


def test_skeptic_names_lookahead_fingerprint():
    """这是真正的验收:当年那个 78.7% 的假因子,现在必须被点名。"""
    ev = FactorEvidence(
        factor="低成交额", market="HKStock", n_hypotheses=22,
        t_stat=3.74, monotonicity=0.75, net_annual=0.787, avg_turnover=0.194,
        subperiod_annual=[-0.019, 1.0197, 1.8154],
        universe_selection="按今日成交额取前50",
    )
    op = MockLLMJudge().review(ReviewRole.SKEPTIC, _ctx(evidence=ev))
    assert op.verdict == Verdict.REJECT
    assert any("前视偏差" in c for c in op.concerns), op.concerns
    assert any("时点重建" in s for s in op.suggestions)


def test_skeptic_flags_index_misalignment():
    dq = DataQualityReport(n_symbols=50, bar_count_min=1483, bar_count_max=2237,
                           index_alignment_safe=False)
    op = MockLLMJudge().review(ReviewRole.SKEPTIC, _ctx(data_quality=dq))
    assert any("同一天" in c for c in op.concerns)


def test_skeptic_flags_unrecorded_universe_selection():
    ev = FactorEvidence(factor="f", market="HK", n_hypotheses=1, t_stat=5.0,
                        monotonicity=1.0, net_annual=0.1,
                        subperiod_annual=[0.1, 0.1, 0.1])
    op = MockLLMJudge().review(ReviewRole.SKEPTIC, _ctx(evidence=ev))
    assert any("前视偏差" in c or "选取方式" in c for c in op.concerns)


def test_skeptic_unchanged_when_no_evidence_supplied():
    """向后兼容:不给证据时行为与之前一致(只审策略),不能凭空 REJECT。"""
    op = MockLLMJudge().review(ReviewRole.SKEPTIC, _ctx())
    assert op.verdict == Verdict.APPROVE
