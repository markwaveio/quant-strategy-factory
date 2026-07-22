"""策略生成器:洞察 → 策略提案。

映射规则(把「市场在发生什么」翻译成「该上哪类策略」):
- 走强 / 有正动量        → 趋势策略(顺势)
- 高波动 / 结构切换      → 均值回归(赚震荡)
- 黑天鹅 / 情绪极端事件  → 新闻事件策略(规避或反弹)

即使没有洞察,也会给每个标的兜底生成一个趋势提案,保证流水线有输入。
"""

from __future__ import annotations

import uuid

from qsf.core.schema import (
    Insight,
    InsightKind,
    MarketSnapshot,
    StrategyFamily,
    StrategyProposal,
)
from qsf.strategy.base import Strategy
from qsf.strategy.families import (
    MeanReversionStrategy,
    NewsEventStrategy,
    TrendStrategy,
)

_FAMILY_IMPL: dict[StrategyFamily, type[Strategy]] = {
    StrategyFamily.TREND: TrendStrategy,
    StrategyFamily.MEAN_REVERSION: MeanReversionStrategy,
    StrategyFamily.NEWS_EVENT: NewsEventStrategy,
}


def build_strategy(proposal: StrategyProposal) -> Strategy:
    """把提案实例化成可回测的策略对象。"""
    impl = _FAMILY_IMPL[proposal.family]
    return impl(proposal)


class StrategyGenerator:
    def __init__(self, *, max_per_symbol: int = 3, searcher=None) -> None:
        """`searcher`:传入 `ParamSearcher` 则参数来自**网格搜索**;
        为 None 时用下方基线默认值 —— 那些默认值**未经搜索**,
        仅供离线 smoke 跑通流程,不可作为投资依据。
        """
        self.max_per_symbol = max_per_symbol
        self.searcher = searcher
        self.search_info: dict[str, object] = {}   # family -> ParamSearchInfo

    def _new_id(self, family: StrategyFamily, symbol: str) -> str:
        return f"{family.value}:{symbol}:{uuid.uuid4().hex[:6]}"

    def generate(
        self, snapshot: MarketSnapshot, insights: list[Insight]
    ) -> list[StrategyProposal]:
        by_symbol: dict[str, list[Insight]] = {}
        for ins in insights:
            by_symbol.setdefault(ins.symbol, []).append(ins)

        # 若配了 searcher,先在全池上搜出各族的稳健参数(一次搜索,全池复用)
        searched: dict[str, dict] = {}
        if self.searcher is not None:
            for fam in ("trend", "mean_reversion"):
                params, info, _ = self.searcher.search(fam, snapshot.symbols, snapshot)
                if params:
                    searched[fam] = params
                    self.search_info[fam] = info

        def _params(fam: str, default: dict) -> dict:
            return dict(searched.get(fam, default))

        proposals: list[StrategyProposal] = []
        for symbol in snapshot.symbols:
            feat = snapshot.features.get(symbol)
            sym_insights = by_symbol.get(symbol, [])
            kinds = {i.kind for i in sym_insights}
            summaries = [i.summary for i in sym_insights]
            made: list[StrategyProposal] = []

            # 趋势:走强或有正动量
            mom = (feat.momentum or 0.0) if feat else 0.0
            if InsightKind.STRENGTH in kinds or mom > 0.03:
                made.append(
                    StrategyProposal(
                        id=self._new_id(StrategyFamily.TREND, symbol),
                        family=StrategyFamily.TREND,
                        symbol=symbol,
                        rationale="标的走强/正动量,顺势做趋势跟随",
                        params=_params("trend", {"fast": 10, "slow": 30}),
                        triggered_by=summaries,
                    )
                )

            # 均值回归:高波动或结构切换
            vol = (feat.volatility or 0.0) if feat else 0.0
            if InsightKind.REGIME_SHIFT in kinds or vol > 0.35:
                made.append(
                    StrategyProposal(
                        id=self._new_id(StrategyFamily.MEAN_REVERSION, symbol),
                        family=StrategyFamily.MEAN_REVERSION,
                        symbol=symbol,
                        rationale="波动放大/震荡市,做均值回归",
                        params=_params("mean_reversion", {"window": 20, "entry_z": 1.0}),
                        triggered_by=summaries,
                    )
                )

            # 新闻事件:黑天鹅或极端情绪
            if InsightKind.EVENT in kinds:
                swan = (feat.black_swan_score if feat else 0.0)
                # 找到黑天鹅相对位置:mock 里写死在倒数第 12 天附近,这里用特征估一个
                shock_index = 12 if swan > 0 else 0
                made.append(
                    StrategyProposal(
                        id=self._new_id(StrategyFamily.NEWS_EVENT, symbol),
                        family=StrategyFamily.NEWS_EVENT,
                        symbol=symbol,
                        rationale="事件冲击后博反弹(或规避),典型事件驱动",
                        params={"shock_index": shock_index, "hold_days": 10, "mode": 1},
                        triggered_by=summaries,
                    )
                )

            # 兜底:该标的一个提案都没有,给个保守趋势提案
            if not made:
                made.append(
                    StrategyProposal(
                        id=self._new_id(StrategyFamily.TREND, symbol),
                        family=StrategyFamily.TREND,
                        symbol=symbol,
                        rationale="无显著信号,给出基线趋势提案供评估",
                        params=_params("trend", {"fast": 10, "slow": 30}),
                        triggered_by=[],
                    )
                )

            proposals.extend(made[: self.max_per_symbol])

        return proposals
