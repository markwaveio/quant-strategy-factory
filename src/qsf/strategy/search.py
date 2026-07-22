"""参数搜索:让策略参数来自**搜索**,而不是拍脑袋。

为什么必须有这个模块:
    用 `fast=10/slow=30` 单点回测某标的得出「亏 21%」,**什么都没证明** ——
    换组参数可能就是赚的。未经搜索的回测结论不成立,质疑者理应否决。

核心原则(与 strategy-factory skill 一致):
    **单标的最优 ≈ 过拟合。** 要找的是在标的池里「稳定」的参数,
    宁可平均收益低一点,也要方差小、覆盖率高。

    排序优先级:覆盖率 > 最差表现 > 均值。

注意:本模块用的是 `qsf.backtest` 的 **smoke backtest**,只适合
粗筛与相对比较。选出候选后,应交给 QuantDinger 服务端回测复核。
"""

from __future__ import annotations

import itertools
import statistics as st
from dataclasses import dataclass, field

from qsf.backtest.engine import BacktestEngine
from qsf.core.schema import MarketSnapshot, ParamSearchInfo, StrategyProposal

# 各策略族的默认搜索空间。刻意保持粗粒度 —— 相邻值要有经济含义上的区分度,
# 扫 fast∈{10,11,12} 这种无意义邻域只会浪费预算并制造参数尖峰的假象。
DEFAULT_SPACES: dict[str, dict[str, list]] = {
    "trend": {"fast": [5, 8, 10, 15, 20], "slow": [20, 30, 40, 60]},
    "mean_reversion": {"window": [10, 15, 20, 30], "entry_z": [0.8, 1.0, 1.5, 2.0]},
    "news_event": {"hold_days": [5, 10, 15, 20]},
}


@dataclass
class ParamResult:
    """一组参数在标的池上的综合表现。"""

    params: dict[str, float]
    per_symbol: dict[str, float] = field(default_factory=dict)  # symbol -> 夏普
    n_trades: dict[str, int] = field(default_factory=dict)

    @property
    def coverage(self) -> float:
        """在几成标的上夏普为正 —— 通用性的直接度量。"""
        if not self.per_symbol:
            return 0.0
        return sum(1 for v in self.per_symbol.values() if v > 0) / len(self.per_symbol)

    @property
    def worst(self) -> float:
        return min(self.per_symbol.values()) if self.per_symbol else 0.0

    @property
    def mean(self) -> float:
        return st.mean(self.per_symbol.values()) if self.per_symbol else 0.0

    @property
    def spread(self) -> float:
        """标的间的离散度,越小越稳健。"""
        v = list(self.per_symbol.values())
        return st.stdev(v) if len(v) > 1 else 0.0

    @property
    def min_trades(self) -> int:
        return min(self.n_trades.values()) if self.n_trades else 0

    def sort_key(self) -> tuple:
        """排序键:覆盖率 > 最差 > 均值。**绝不按单标的最优排。**"""
        return (self.coverage, self.worst, self.mean)


def _expand(space: dict[str, list]) -> list[dict]:
    """参数空间 → 所有组合。"""
    if not space:
        return [{}]
    keys = list(space)
    return [dict(zip(keys, combo, strict=True))
            for combo in itertools.product(*(space[k] for k in keys))]


def _is_valid(family: str, params: dict) -> bool:
    """剔除无意义组合,省搜索预算。"""
    if family == "trend":
        # 快线必须显著短于慢线,否则双均线没有意义
        return params.get("fast", 0) < params.get("slow", 0) * 0.8
    return True


class ParamSearcher:
    """在标的池上网格搜索,按稳健性挑参数。"""

    def __init__(self, engine: BacktestEngine, *, max_variants: int = 48) -> None:
        self.engine = engine
        self.max_variants = max_variants

    def search(
        self,
        family: str,
        symbols: list[str],
        snapshot: MarketSnapshot,
        space: dict[str, list] | None = None,
        *,
        min_trades: int = 5,
    ) -> tuple[dict[str, float], ParamSearchInfo, list[ParamResult]]:
        """返回 (最稳健的参数, 搜索元信息, 全部结果按稳健性排序)。

        `min_trades`:任一标的交易数低于此值的参数组直接淘汰 —— 样本不足的
        漂亮数字没有意义(与质疑者同口径)。
        """
        space = space or DEFAULT_SPACES.get(family, {})
        combos = [c for c in _expand(space) if _is_valid(family, c)][: self.max_variants]

        results: list[ParamResult] = []
        for params in combos:
            r = ParamResult(params=params)
            for sym in symbols:
                if sym not in snapshot.prices:
                    continue
                proposal = StrategyProposal(
                    id=f"search:{family}:{sym}", family=family,  # type: ignore[arg-type]
                    symbol=sym, rationale="参数搜索", params=params,
                )
                m = self.engine.run(proposal, snapshot)
                r.per_symbol[sym] = m.sharpe
                r.n_trades[sym] = m.n_trades
            if r.per_symbol:
                results.append(r)

        # 样本不足的先淘汰;若全被淘汰则保留原集合(总比无解好,但会在 info 里体现)
        viable = [r for r in results if r.min_trades >= min_trades] or results
        viable.sort(key=lambda r: r.sort_key(), reverse=True)

        best = viable[0] if viable else None
        info = ParamSearchInfo(
            searched=True,
            space={k: list(v) for k, v in space.items()},
            n_evaluated=len(results),
            method="grid",
            pool_symbols=list(symbols),
            pool_coverage=round(best.coverage, 3) if best else None,
            pool_worst=round(best.worst, 4) if best else None,
        )
        return (best.params if best else {}), info, viable

    @staticmethod
    def diagnose(results: list[ParamResult]) -> list[str]:
        """危险信号检查 —— 与 strategy-factory skill 的四条一致。"""
        warns: list[str] = []
        if not results:
            return ["搜索无有效结果"]
        best = results[0]
        if best.coverage < 0.5:
            warns.append(
                f"最优参数仅在 {best.coverage:.0%} 的标的上为正,通用性不足"
            )
        if len(best.per_symbol) > 1 and best.spread > abs(best.mean or 1) * 1.5:
            warns.append(
                f"标的间表现离散(std {best.spread:.2f} vs 均值 {best.mean:.2f}),疑似巧合"
            )
        if best.min_trades < 10:
            warns.append(f"最少交易数仅 {best.min_trades} 笔,统计不显著")
        # 参数尖峰检测:最优与次优差距过大 → 参数敏感,实盘易失效
        if len(results) > 1:
            gap = best.mean - results[1].mean
            if abs(best.mean) > 1e-9 and gap > abs(best.mean) * 0.5:
                warns.append("最优与次优参数差距悬殊,疑似参数尖峰而非高原")
        return warns
