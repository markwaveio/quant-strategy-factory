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
    assert "# 富途运行备注:" in text
    assert "#   1. 标的选择: 每次回测/模拟只选择上方标的池中的一个标的" in text
    assert "#   2. 回测区间: 建议 最近 500 个交易日/约 2 年" in text
    assert "#   3. 初始资金: 建议 100000 HKD" in text
    assert "#   4. K线规则:  使用 日 K / RTH / CLOSE" in text
    assert "#   5. 运行参数: 快线=10,慢线=20" in text
    assert "#   6. 开仓数量: 默认 1 手" in text
    assert "class Strategy(StrategyBase)" in text
