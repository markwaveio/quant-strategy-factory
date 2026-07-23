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
    ResearchReport,
    ReviewNote,
    ScanCandidate,
    StrategyFamily,
    StrategyProposal,
    Verdict,
)
from qsf.data.provider import DataProvider, build_data_provider
from qsf.decision.decider import Decider
from qsf.evaluation.committee import EvaluationBoard
from qsf.insight.engine import InsightEngine
from qsf.memory.store import MemoryStore
from qsf.research.reporter import ResearchReporter
from qsf.review.reviewer import Reviewer
from qsf.scan import DailyScanner
from qsf.strategy.generator import StrategyGenerator


@dataclass
class PipelineResult:
    """一次完整运行的所有中间产物 + 最终产物,方便调试与展示。"""

    run_id: str
    snapshot: MarketSnapshot
    scan_candidates: list[ScanCandidate] = field(default_factory=list)
    insights: list[Insight] = field(default_factory=list)
    proposals: list[StrategyProposal] = field(default_factory=list)
    evaluations: list[EvaluationResult] = field(default_factory=list)
    backtests: dict[str, BacktestMetrics] = field(default_factory=dict)
    report: DecisionReport | None = None
    review: ReviewNote | None = None
    research_report: ResearchReport | None = None


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
        research_reporter: ResearchReporter | None = None,
        scanner: DailyScanner | None = None,
    ) -> None:
        self.config = config
        self.data_provider = data_provider or build_data_provider(config.data)
        self.insight_engine = insight_engine or InsightEngine()
        if strategy_generator is not None:
            self.strategy_generator = strategy_generator
        elif config.strategy.param_search:
            # 参数来自网格搜索 —— 复用同一个 smoke 回测引擎
            from qsf.strategy.search import ParamSearcher
            self.strategy_generator = StrategyGenerator(
                searcher=ParamSearcher(
                    BacktestEngine(config.backtest),
                    max_variants=config.strategy.max_variants,
                )
            )
        else:
            self.strategy_generator = StrategyGenerator()
        self.evaluation_board = evaluation_board or EvaluationBoard(config.evaluation)
        self.backtest_engine = backtest_engine or BacktestEngine(config.backtest)
        self.memory_store = memory_store or MemoryStore(config.memory_path)
        self.decider = decider or Decider(config.evaluation)
        self.reviewer = reviewer or Reviewer()
        self.research_reporter = research_reporter or ResearchReporter()
        self.scanner = scanner or DailyScanner(config.scan)

    def run(self) -> PipelineResult:
        run_id = f"{self.config.run_id}-{uuid.uuid4().hex[:8]}"

        # 1. 数据层:采集 → 标准化 → 特征提取
        snapshot = self.data_provider.build_snapshot()
        result = PipelineResult(run_id=run_id, snapshot=snapshot)

        # 2. 洞察层:异常 / 走强 / 结构切换
        result.insights = self.insight_engine.analyze(snapshot)

        working_snapshot = snapshot
        allowed_families_by_symbol: dict[str, set[StrategyFamily]] | None = None
        if self.config.scan.enabled:
            result.scan_candidates = self.scanner.scan(snapshot, result.insights)
            working_snapshot = self._candidate_snapshot(snapshot, result.scan_candidates)
            allowed_families_by_symbol = {
                c.symbol: set(c.recommended_families) for c in result.scan_candidates
            }
            result.insights = [
                i for i in result.insights if i.symbol in working_snapshot.prices
            ]

        # 3. 策略生成层:趋势 / 均值回归 / 新闻事件
        result.proposals = self.strategy_generator.generate(
            working_snapshot, result.insights, allowed_families_by_symbol
        )

        # 4~6. 逐个策略:AI 评审 + 回测(评审用到回测结果,回测先行)
        past_memory = self.memory_store.all()
        n_cand = len(result.proposals)
        for proposal in result.proposals:
            metrics = self.backtest_engine.run(proposal, snapshot)
            # 让评审知道这是从多少个候选里挑的 —— 挑最大值必然乐观
            metrics.n_candidates = n_cand
            result.backtests[proposal.id] = metrics

            evaluation = self.evaluation_board.evaluate(
                proposal, metrics, snapshot, past_memory
            )
            result.evaluations.append(evaluation)

            # 5. 记忆层:把这次结果沉淀为经验(无论成败)
            self.memory_store.remember(proposal, metrics, snapshot, evaluation)

        # 7. 决策层:综合评审 + 回测,产出决策报告
        result.report = self.decider.decide(
            working_snapshot, result.insights, result.proposals,
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

        # 研究证据包:用于策略发现、复验和模拟交易前的候选归档
        result.research_report = self.research_reporter.build(
            snapshot=working_snapshot,
            proposals=result.proposals,
            evaluations=result.evaluations,
            backtests=result.backtests,
            decision_report=result.report,
        )

        return result

    @staticmethod
    def _candidate_snapshot(
        snapshot: MarketSnapshot, candidates: list[ScanCandidate]
    ) -> MarketSnapshot:
        symbols = {c.symbol for c in candidates}
        return MarketSnapshot(
            as_of=snapshot.as_of,
            prices={s: p for s, p in snapshot.prices.items() if s in symbols},
            news=[
                n for n in snapshot.news
                if not n.symbols or any(s in symbols for s in n.symbols)
            ],
            reports={s: r for s, r in snapshot.reports.items() if s in symbols},
            features={s: f for s, f in snapshot.features.items() if s in symbols},
            quality=snapshot.quality,
        )

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
            "candidates": len(result.scan_candidates),
            "proposals": len(result.proposals),
            "approved": approved,
            "rejected": rejected,
            "decisions": len(result.report.decisions) if result.report else 0,
        }
