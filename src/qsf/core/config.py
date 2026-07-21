"""流水线配置。

优先级:显式传参 > YAML 文件 > 代码默认值。
密钥永远不写进 YAML,只从环境变量读(见 `llm_provider` 相关字段)。
"""

from __future__ import annotations

import os
from pathlib import Path

import yaml
from pydantic import BaseModel, Field


class DataConfig(BaseModel):
    mode: str = "mock"                       # mock | live
    symbols: list[str] = Field(default_factory=lambda: ["DEMO.A", "DEMO.B", "DEMO.C"])
    lookback_days: int = 250
    seed: int = 42                           # mock 数据的随机种子,保证可复现


class EvaluationConfig(BaseModel):
    provider: str = "mock"                   # mock | anthropic
    model: str = "claude-opus-4-8"           # 真实评审时用最新 Claude 模型
    roles: list[str] = Field(
        default_factory=lambda: ["skeptic", "risk_officer", "committee"]
    )
    # 综合评估通过阈值
    approve_threshold: float = 0.6
    # 风险官红线:最大回撤超过该值(绝对值)直接否决
    max_drawdown_limit: float = 0.25


class BacktestConfig(BaseModel):
    initial_cash: float = 1_000_000.0
    fee_bps: float = 5.0                     # 单边手续费(万分之)
    slippage_bps: float = 2.0                # 滑点(万分之)
    risk_free_rate: float = 0.02             # 年化无风险利率,用于夏普


class Config(BaseModel):
    """流水线总配置。"""

    run_id: str = "adhoc"
    data: DataConfig = Field(default_factory=DataConfig)
    evaluation: EvaluationConfig = Field(default_factory=EvaluationConfig)
    backtest: BacktestConfig = Field(default_factory=BacktestConfig)
    memory_path: str = "data_store/memory/memory.jsonl"
    report_dir: str = "reports"

    @classmethod
    def from_yaml(cls, path: str | Path) -> Config:
        data = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
        return cls(**data)

    @property
    def anthropic_api_key(self) -> str | None:
        """真实评审时从环境变量读取,绝不落库。"""
        return os.getenv("ANTHROPIC_API_KEY")
