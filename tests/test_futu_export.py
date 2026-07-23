import json

from scripts.export_futu_strategy import main


def test_export_futu_strategy_from_candidates_json(tmp_path):
    payload = {
        "strategies": [
            {
                "strategy_id": "trend:00941:abc123",
                "symbol": "00941",
                "family": "trend",
                "status": "promote_to_sim",
                "params": {"fast": 10, "slow": 20},
                "final_verdict": "approve",
                "warnings": [],
                "next_step": "进入富途回测验证",
            },
            {
                "strategy_id": "mean:00001:def456",
                "symbol": "00001",
                "family": "mean_reversion",
                "status": "reject",
                "params": {"window": 20, "entry_z": 1.5},
            },
        ]
    }
    src = tmp_path / "strategies.json"
    out = tmp_path / "futu"
    src.write_text(json.dumps(payload), encoding="utf-8")

    code = main(["--from-strategies", str(src), "--output-dir", str(out), "--approved-only"])

    assert code == 0
    files = list(out.glob("*.py"))
    assert len(files) == 1
    text = files[0].read_text(encoding="utf-8")
    assert "show_variable(10, GlobalType.INT)" in text
    assert "show_variable(20, GlobalType.INT)" in text
    assert "class Strategy(StrategyBase)" in text
