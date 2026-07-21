"""流水线编排器:把 8 层按顺序串起来。

    数据 → 洞察 → 策略生成 → 评估(AI) → 回测 → 记忆 → 决策 → 复盘

每层都通过依赖注入传入,默认用各层的默认实现;
要替换某一层(例如把 mock 数据换成真实源),只需传入另一个实现即可,
不必改动本文件。
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import date

from qsf.backtest.engine import BacktestEngine
from qsf.core.config import Config
from qsf.core.schema import (
    BacktestMetrics,
    DecisionReport,
    EvaluationResult,
    Insight,
    MarketSnapshot,
    ReviewNote,
    StrategyProposal,
    Verdict,
)
from qsf.data.provider import DataProvider, MockDataProvider
from qsf.decision.decider import Decider
from qsf.evaluation.committee import EvaluationBoard
from qsf.insight.engine import InsightEngine
from qsf.memory.store import MemoryStore
from qsf.review.reviewer import Reviewer
from qsf.strategy.generator import StrategyGenerator


@dataclass
class PipelineResult:
    """一次完整运行的所有中间产物 + 最终产物,方便调试与展示。"""

    run_id: str
    snapshot: MarketSnapshot
    insights: list[Insight] = field(default_factory=list)
    proposals: list[StrategyProposal] = field(default_factory=list)
    evaluations: list[EvaluationResult] = field(default_factory=list)
    backtests: dict[str, BacktestMetrics] = field(default_factory=dict)
    report: DecisionReport | None = None
    review: ReviewNote | None = None


class Pipeline:
    """量化策略工厂主流程。"""

    def __init__(
        self,
        config: Config,
        *,
        data_provider: DataProvider | None = None,
        insight_engine: InsightEngine | None = None,
        strategy_generator: StrategyGenerator | None = None,
        evaluation_board: EvaluationBoard | None = None,
        backtest_engine: BacktestEngine | None = None,
        memory_store: MemoryStore | None = None,
        decider: Decider | None = None,
        reviewer: Reviewer | None = None,
    ) -> None:
        self.config = config
        self.data_provider = data_provider or MockDataProvider(config.data)
        self.insight_engine = insight_engine or InsightEngine()
        self.strategy_generator = strategy_generator or StrategyGenerator()
        self.evaluation_board = evaluation_board or EvaluationBoard(config.evaluation)
        self.backtest_engine = backtest_engine or BacktestEngine(config.backtest)
        self.memory_store = memory_store or MemoryStore(config.memory_path)
        self.decider = decider or Decider(config.evaluation)
        self.reviewer = reviewer or Reviewer()

    def run(self) -> PipelineResult:
        run_id = f"{self.config.run_id}-{uuid.uuid4().hex[:8]}"

        # 1. 数据层:采集 → 标准化 → 特征提取
        snapshot = self.data_provider.build_snapshot()
        result = PipelineResult(run_id=run_id, snapshot=snapshot)

        # 2. 洞察层:异常 / 走强 / 结构切换
        result.insights = self.insight_engine.analyze(snapshot)

        # 3. 策略生成层:趋势 / 均值回归 / 新闻事件
        result.proposals = self.strategy_generator.generate(snapshot, result.insights)

        # 4~6. 逐个策略:AI 评审 + 回测(评审用到回测结果,回测先行)
        past_memory = self.memory_store.all()
        for proposal in result.proposals:
            metrics = self.backtest_engine.run(proposal, snapshot)
            result.backtests[proposal.id] = metrics

            evaluation = self.evaluation_board.evaluate(
                proposal, metrics, snapshot, past_memory
            )
            result.evaluations.append(evaluation)

            # 5. 记忆层:把这次结果沉淀为经验(无论成败)
            self.memory_store.remember(proposal, metrics, snapshot, evaluation)

        # 7. 决策层:综合评审 + 回测,产出决策报告
        result.report = self.decider.decide(
            snapshot, result.insights, result.proposals,
            result.evaluations, result.backtests,
        )

        # 8. 复盘层
        result.review = self.reviewer.review(
            run_id=run_id,
            run_date=date.today(),
            proposals=result.proposals,
            evaluations=result.evaluations,
            backtests=result.backtests,
        )

        return result

    # 便捷统计 ------------------------------------------------------------- #

    @staticmethod
    def summarize(result: PipelineResult) -> dict[str, int]:
        approved = sum(
            1 for e in result.evaluations if e.final_verdict == Verdict.APPROVE
        )
        rejected = sum(
            1 for e in result.evaluations if e.final_verdict == Verdict.REJECT
        )
        return {
            "insights": len(result.insights),
            "proposals": len(result.proposals),
            "approved": approved,
            "rejected": rejected,
            "decisions": len(result.report.decisions) if result.report else 0,
        }
