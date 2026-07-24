import json

from scripts.curate_reports import main


def test_curate_reports_ranks_promoted_candidates_and_archives_stale(tmp_path):
    reports = tmp_path / "reports"
    reports.mkdir()
    (reports / "futu-oos").mkdir()
    (reports / "factory-oos").mkdir()
    (reports / "factory").mkdir()

    old = reports / "run-opend-hk.md"
    old.write_text("old", encoding="utf-8")
    stale = reports / "factory" / "strategy-cards-v1.md"
    stale.write_text("v1", encoding="utf-8")
    card = reports / "factory-oos" / "strategy-cards-v2.md"
    card.write_text("v2", encoding="utf-8")
    futu = reports / "futu-oos" / "futu-01177-trend-fast8-slow20.py"
    futu.write_text("code", encoding="utf-8")
    strategies = reports / "factory-oos" / "strategies-v2.json"
    strategies.write_text(
        json.dumps(
            {
                "strategies": [
                    {
                        "strategy_id": "trend:01177:aaa",
                        "symbol": "01177",
                        "family": "trend",
                        "status": "promote_to_sim",
                        "action": "buy",
                        "params": {"fast": 8, "slow": 20},
                        "final_verdict": "approve",
                        "aggregate_score": 1.0,
                        "triggered_by": ["扫描独立印证"],
                        "metrics": {
                            "oos_excess": 0.222,
                            "max_drawdown": -0.12,
                            "n_trades": 14,
                        },
                    },
                    {
                        "strategy_id": "trend:00941:bbb",
                        "symbol": "00941",
                        "family": "trend",
                        "status": "watchlist",
                        "action": "avoid",
                        "params": {"fast": 8, "slow": 20},
                        "final_verdict": "reject",
                        "aggregate_score": 0.2,
                        "metrics": {"oos_excess": -0.01, "max_drawdown": -0.09, "n_trades": 20},
                    },
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    code = main(["--reports-dir", str(reports), "--archive-stale"])

    assert code == 0
    index = (reports / "README.md").read_text(encoding="utf-8")
    assert "最优候选:01177 `fast=8, slow=20`" in index
    assert "推荐指数:" in index
    assert "机会指数:" in index
    assert "futu-oos/futu-01177-trend-fast8-slow20.py" in index
    assert not old.exists()
    assert not stale.exists()
    assert card.exists()
    assert futu.exists()
    assert list((reports / "_archive").glob("**/run-opend-hk.md"))
