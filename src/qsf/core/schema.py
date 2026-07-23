"""贯穿全流水线的数据契约。

设计原则:
- 每一层只依赖这里定义的类型,不依赖其他层的内部实现。
- 全部用 pydantic 模型,自带校验 + JSON 序列化,方便落盘和跨进程传递。
- 字段尽量「面向决策」——最终服务于人能看懂的一份决策报告。
"""

from __future__ import annotations

from datetime import date, datetime
from enum import Enum

from pydantic import BaseModel, Field

# --------------------------------------------------------------------------- #
# 1. 数据层                                                                     #
# --------------------------------------------------------------------------- #


class AssetClass(str, Enum):
    """标的大类。"""

    A_SHARE = "a_share"
    US_EQUITY = "us_equity"
    CRYPTO = "crypto"
    OTHER = "other"


class OHLCV(BaseModel):
    """单根 K 线(标准化后的行情最小单元)。"""

    ts: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float


class PriceSeries(BaseModel):
    """某标的一段时间的行情序列(标准化产物)。"""

    symbol: str
    asset_class: AssetClass
    bars: list[OHLCV]

    @property
    def closes(self) -> list[float]:
        return [b.close for b in self.bars]


class NewsItem(BaseModel):
    """一条标准化新闻。"""

    ts: datetime
    title: str
    body: str = ""
    source: str = ""
    symbols: list[str] = Field(default_factory=list)  # 关联标的
    url: str = ""


class FinancialReport(BaseModel):
    """一份标准化财报快照(季度/年度)。"""

    symbol: str
    period: str  # 例如 "2026Q1"
    revenue: float | None = None
    net_profit: float | None = None
    eps: float | None = None
    roe: float | None = None
    debt_ratio: float | None = None


class FeatureSet(BaseModel):
    """特征提取产物:把行情+新闻+财报浓缩成可供上层消费的指标。"""

    symbol: str
    as_of: datetime
    # 行情类
    volatility: float | None = None          # 年化波动率
    momentum: float | None = None            # 近 N 日动量
    trend_strength: float | None = None      # 趋势强度(-1~1)
    # 情绪 / 新闻类
    sentiment: float | None = None           # 综合情绪 (-1~1)
    keywords: list[str] = Field(default_factory=list)
    black_swan_score: float = 0.0            # 黑天鹅事件强度 (0~1)
    # 基本面类
    fundamentals: dict[str, float] = Field(default_factory=dict)
    extra: dict[str, float] = Field(default_factory=dict)


class MarketSnapshot(BaseModel):
    """数据层的总产物:某个时点、某个标的池的完整快照。

    这是喂给「洞察层」的唯一入口。
    """

    as_of: datetime
    prices: dict[str, PriceSeries] = Field(default_factory=dict)      # symbol -> series
    news: list[NewsItem] = Field(default_factory=list)
    reports: dict[str, FinancialReport] = Field(default_factory=dict)  # symbol -> report
    features: dict[str, FeatureSet] = Field(default_factory=dict)      # symbol -> features
    # 数据体检报告随快照一路流到评估层 —— 否则质疑者拿不到,新加的
    # 数据审查就是空转(实测:接进 provider 后流水线里依然没人传它)。
    # 前向引用:DataQualityReport 定义在文件末尾的数据质量章节。
    quality: DataQualityReport | None = None

    @property
    def symbols(self) -> list[str]:
        return sorted(self.prices.keys())


# --------------------------------------------------------------------------- #
# 2. 洞察层                                                                     #
# --------------------------------------------------------------------------- #


class InsightKind(str, Enum):
    ANOMALY = "anomaly"          # 异常
    STRENGTH = "strength"        # 走强
    WEAKNESS = "weakness"        # 走弱
    REGIME_SHIFT = "regime_shift"  # 市场结构切换
    EVENT = "event"              # 事件驱动(新闻/黑天鹅)


class Insight(BaseModel):
    """一条市场洞察。"""

    kind: InsightKind
    symbol: str
    as_of: datetime
    score: float                 # 显著性 0~1
    summary: str                 # 人可读的一句话
    evidence: dict[str, float] = Field(default_factory=dict)  # 支撑该洞察的量化证据


# --------------------------------------------------------------------------- #
# 3. 策略生成层                                                                 #
# --------------------------------------------------------------------------- #


class StrategyFamily(str, Enum):
    TREND = "trend"                 # 趋势跟随
    MEAN_REVERSION = "mean_reversion"  # 均值回归
    NEWS_EVENT = "news_event"       # 新闻事件驱动


class StrategyProposal(BaseModel):
    """一个候选策略提案(尚未回测、尚未评审)。"""

    id: str
    family: StrategyFamily
    symbol: str
    rationale: str                          # 为什么在此刻提出该策略
    params: dict[str, float] = Field(default_factory=dict)  # 策略参数
    triggered_by: list[str] = Field(default_factory=list)   # 关联的洞察 summary


class ScanCandidate(BaseModel):
    symbol: str
    score: float
    reasons: list[str] = Field(default_factory=list)
    evidence: dict[str, float] = Field(default_factory=dict)
    recommended_families: list[StrategyFamily] = Field(default_factory=list)


# --------------------------------------------------------------------------- #
# 4. 评估层(AI 挑战)                                                          #
# --------------------------------------------------------------------------- #


class ReviewRole(str, Enum):
    SKEPTIC = "skeptic"          # 质疑者:过拟合、参数拍脑袋
    RISK_OFFICER = "risk_officer"  # 风险官:最坏亏多少
    COMMITTEE = "committee"      # 综合评估官:投资委员会


class Verdict(str, Enum):
    APPROVE = "approve"
    REVISE = "revise"
    REJECT = "reject"


class ReviewOpinion(BaseModel):
    """单个 AI 角色对某策略的评审意见。"""

    role: ReviewRole
    strategy_id: str
    verdict: Verdict
    confidence: float            # 0~1
    concerns: list[str] = Field(default_factory=list)
    suggestions: list[str] = Field(default_factory=list)
    rationale: str = ""


class EvaluationResult(BaseModel):
    """一个策略经过全部 AI 角色评审后的汇总结论。"""

    strategy_id: str
    opinions: list[ReviewOpinion]
    final_verdict: Verdict
    risk_flags: list[str] = Field(default_factory=list)
    aggregate_score: float = 0.0  # 综合评分 0~1


# --------------------------------------------------------------------------- #
# 6. 回测层                                                                     #
# --------------------------------------------------------------------------- #


class BacktestMetrics(BaseModel):
    """回测绩效指标。

    ⚠️ 这是 **smoke backtest** 的产出——用于快速淘汰明显不行的策略,
    不是实盘级绩效。未建模的约束见 `qsf.backtest.engine` 模块文档。
    真实绩效以 QuantDinger 服务端回测为准。
    """

    strategy_id: str
    total_return: float
    annual_return: float
    max_drawdown: float          # 最大回撤(负数)
    sharpe: float
    win_rate: float
    n_trades: int
    equity_curve: list[float] = Field(default_factory=list)
    # 同期买入持有基准 —— **没有它就分不清 alpha 和 beta**。
    # 实测:09999 策略赚 21.7% 看着不错,同期买入持有赚 25.6%,
    # 策略其实跑输"什么都不做",却拿到评分 1.0 三角色全票通过。
    # None = 未计算(如空回测),不是"基准为零" —— 两者必须可区分,
    # 否则一个亏钱但没算基准的策略会被误报成"跑输买入持有"。
    benchmark_return: float | None = None
    benchmark_sharpe: float | None = None

    # 样本外(后 30%)表现 —— **最决定性的一关**。
    # 实测 09618:训练段超额 +70.8%,检验段 -29.2%。全部超额都在样本内,
    # 样本外一分没赚而躺着不动赚 28.5%。只看全样本会把它当成好策略。
    oos_return: float | None = None
    oos_benchmark_return: float | None = None
    oos_sharpe: float | None = None
    # 该策略是从多少个候选里选出来的 —— 挑最大值必然乐观
    n_candidates: int = 1

    @property
    def excess_return(self) -> float | None:
        if self.benchmark_return is None:
            return None
        return round(self.total_return - self.benchmark_return, 4)

    @property
    def oos_excess(self) -> float | None:
        if self.oos_return is None or self.oos_benchmark_return is None:
            return None
        return round(self.oos_return - self.oos_benchmark_return, 4)
    # 最终目标仓位:>0 多头 / <0 空头 / 0 空仓。
    # 决策层据此推导买卖方向 —— 不能只看策略族,因为同一族既可能做多也可能做空。
    final_position: float = 0.0


# --------------------------------------------------------------------------- #
# 5. 记忆层                                                                     #
# --------------------------------------------------------------------------- #


class Outcome(str, Enum):
    SUCCESS = "success"
    FAILURE = "failure"
    NEUTRAL = "neutral"


class BacktestProvenance(BaseModel):
    """回测溯源:让「当时那个结论」日后可复现、可比较。

    没有这些字段,复盘时无法回答「为什么当初判断有效、现在却不行」——
    可能是市场变了,也可能只是数据版本或回测配置换了。
    """

    engine: str = "qsf-smoke"          # qsf-smoke | quantdinger
    config_hash: str = ""              # 回测配置指纹(手续费/滑点/初始资金等)
    data_source: str = ""              # mock | quantdinger | ...
    bars: int = 0                      # 用了多少根 K 线
    start_date: str = ""               # 回测区间(YYYY-MM-DD)
    end_date: str = ""
    # 样本外验证信息 —— 没有 OOS 的结论可信度要打折
    oos_enabled: bool = False
    oos_sharpe: float | None = None    # 样本外夏普(与训练集对比可判过拟合)
    train_sharpe: float | None = None


class ParamSearchInfo(BaseModel):
    """参数来源:区分「搜出来的」和「拍脑袋的」。

    质疑者需要这个 —— 未经搜索的参数,其回测结论本身就不可信。
    """

    searched: bool = False                                  # False = 人工指定
    space: dict[str, list] = Field(default_factory=dict)    # 搜过的参数空间
    n_evaluated: int = 0                                    # 实际评估了多少组
    method: str = ""                                        # grid | random | manual
    # 多标的稳健性(单标的最优 ≈ 过拟合,这些字段用来判断是否稳健)
    pool_symbols: list[str] = Field(default_factory=list)
    pool_coverage: float | None = None   # 在几成标的上为正
    pool_worst: float | None = None      # 最差标的的表现


class MemoryRecord(BaseModel):
    """一条经验记忆:某个策略在某种市场条件下的成败。"""

    strategy_id: str
    family: StrategyFamily
    symbol: str
    created_at: datetime
    outcome: Outcome
    metrics: BacktestMetrics | None = None
    market_context: dict[str, float] = Field(default_factory=dict)  # 当时的关键特征
    lesson: str = ""             # 一句话教训 / 经验
    # --- 复盘所需的溯源信息 --- #
    params: dict[str, float] = Field(default_factory=dict)   # 当时用的参数
    param_search: ParamSearchInfo | None = None              # 参数怎么来的
    provenance: BacktestProvenance | None = None             # 回测怎么跑的
    strategy_version: str = "v1"                             # 策略代码版本
    regime: str = ""                                         # 当时市况(趋势/震荡/普涨…)


# --------------------------------------------------------------------------- #
# 7. 决策层                                                                     #
# --------------------------------------------------------------------------- #


class Action(str, Enum):
    BUY = "buy"
    SELL = "sell"
    HOLD = "hold"
    AVOID = "avoid"


class Decision(BaseModel):
    """针对单个标的的最终决策建议。"""

    symbol: str
    action: Action
    strategy_id: str
    conviction: float            # 信心 0~1
    suggested_weight: float      # 建议仓位 0~1
    reasons: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class DecisionReport(BaseModel):
    """决策层总产物:一份给人看的投资决策报告。"""

    as_of: datetime
    decisions: list[Decision]
    market_summary: str = ""
    top_insights: list[str] = Field(default_factory=list)
    disclaimer: str = "本报告由自动化系统生成,仅供研究,不构成投资建议。"


# --------------------------------------------------------------------------- #
# 8. 复盘层                                                                     #
# --------------------------------------------------------------------------- #


class ReviewNote(BaseModel):
    """一次流水线运行结束后的复盘记录。"""

    run_id: str
    run_date: date
    n_proposals: int
    n_approved: int
    n_rejected: int
    best_strategy_id: str | None = None
    best_sharpe: float | None = None
    key_takeaways: list[str] = Field(default_factory=list)
    improvements: list[str] = Field(default_factory=list)


# --------------------------------------------------------------------------- #
# 9. 研究证据包                                                                 #
# --------------------------------------------------------------------------- #


class ResearchStatus(str, Enum):
    """策略研究状态。

    这里刻意不用交易系统语义。工厂的定位是发现和验证策略,不是自动下单。
    `promote_to_sim` 表示证据足够进入模拟交易/外部回测验证,不是实盘批准。
    """

    PROMOTE_TO_SIM = "promote_to_sim"
    WATCHLIST = "watchlist"
    REJECT = "reject"


class StrategyEvidenceCard(BaseModel):
    """单个策略候选的研究证据卡。

    这是给研究流程消费的结构化产物:保留策略参数、证据、评审结论和下一步。
    模拟交易系统可以读取其中 `symbol` / `family` / `params` / `action` 等字段,
    但仍应把它当作候选策略,而不是自动交易指令。
    """

    strategy_id: str
    symbol: str
    family: StrategyFamily
    status: ResearchStatus
    action: Action
    params: dict[str, float] = Field(default_factory=dict)
    hypothesis: str = ""
    triggered_by: list[str] = Field(default_factory=list)
    final_verdict: Verdict
    aggregate_score: float = 0.0
    metrics: BacktestMetrics | None = None
    reasons: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    next_step: str = ""


class ResearchReport(BaseModel):
    """一次运行的策略研究证据包。"""

    as_of: datetime
    summary: str = ""
    cards: list[StrategyEvidenceCard] = Field(default_factory=list)
    disclaimer: str = "本研究证据包仅用于策略研究与模拟验证,不构成投资建议或实盘指令。"


# --------------------------------------------------------------------------- #
# 10. 数据质量 & 横截面证据                                                      #
#                                                                             #
# 这两组契约是补课补出来的。2026-07 的因子研究里,两个 bug(截面按数组下标      #
# 对齐、前复权产生负价)加两个方法论陷阱(IC≠盈亏、选池前视偏差)联手造出了      #
# 「净年化 78.7%、夏普 1.31」的假结果,而八层架构没有任何一层能拦住 ——        #
# 数据层不校验、评估层只质疑策略逻辑、回测层没有横截面能力。                    #
# 详见 docs/factor-research-02.md。                                            #
# --------------------------------------------------------------------------- #


class QualityIssue(BaseModel):
    """一条数据质量问题。`fatal` 的必须阻断流水线,不能只是打日志。"""

    symbol: str
    code: str            # 机器可读:negative_price / impossible_return / ...
    detail: str
    fatal: bool = False


class DataQualityReport(BaseModel):
    """数据体检结论。进入研究/策略生成之前必须先过这一关。"""

    n_symbols: int = 0
    n_dropped: int = 0
    dropped: dict[str, str] = Field(default_factory=dict)   # symbol → 原因
    issues: list[QualityIssue] = Field(default_factory=list)
    # 各标的 bar 数量差异过大 → 绝不能按数组下标做横截面
    bar_count_min: int = 0
    bar_count_max: int = 0
    index_alignment_safe: bool = True

    @property
    def fatal(self) -> list[QualityIssue]:
        return [i for i in self.issues if i.fatal]

    @property
    def ok(self) -> bool:
        return not self.fatal

    def render(self) -> str:
        lines = [f"数据体检:{self.n_symbols} 只,剔除 {self.n_dropped} 只"]
        if not self.index_alignment_safe:
            lines.append(
                f"  ⚠️ bar 数量 {self.bar_count_min}~{self.bar_count_max},"
                f"差异过大 —— 横截面必须按日期对齐,禁止用数组下标"
            )
        for i in self.issues[:10]:
            lines.append(f"  {'✗' if i.fatal else '⚠️'} {i.symbol}: {i.detail}")
        if len(self.issues) > 10:
            lines.append(f"  ... 另 {len(self.issues) - 10} 条")
        return "\n".join(lines)


class QuantileStat(BaseModel):
    """分层回测里某一档的表现。"""

    q: int
    mean: float
    median: float
    annual_median: float
    win_rate: float
    sharpe: float


class FactorEvidence(BaseModel):
    """一个因子的完整证据链 —— 缺任何一项都不足以据此建策略。

    **IC 只是入场券。** 实测低波_60日 IC +0.066 看着像好因子,分层多空却是
    -31%:最差一期 Q5-Q1 = -59.6% 而同期 IC 仍是 +0.28。秩相关看不见涨幅
    大小,钱在尾部。所以判决必须看 `passed` 的四项合取。
    """

    factor: str
    market: str
    universe_tier: str = ""
    n_symbols: int = 0
    n_periods: int = 0
    horizon: int = 0
    date_from: str = ""
    date_to: str = ""

    # ① 预测力
    ic_mean: float = 0.0
    ic_ir: float = 0.0
    t_stat: float = 0.0
    n_hypotheses: int = 1          # 一共检验了多少个假设(用于多重检验修正)

    # ② 单调性
    quantiles: list[QuantileStat] = Field(default_factory=list)
    monotonicity: float = 0.0

    # ③ 扣费后收益
    avg_turnover: float = 0.0
    cost_bps: float = 0.0
    gross_annual: float = 0.0
    net_annual: float = 0.0
    net_sharpe: float = 0.0
    breakeven_bps: float = 0.0

    # ④ 分段稳健(前视偏差探针:只有后段赚钱 = 选池用了未来信息)
    subperiod_annual: list[float] = Field(default_factory=list)

    # 选池方式 —— 陷阱四的根源,必须记录以便质疑者审问
    universe_selection: str = ""

    @property
    def bonferroni_threshold(self) -> float:
        """多重检验修正后的 t 门槛(α=0.05 双侧)。

        检验 22 个假设时门槛是 |t|>3.06,不是 1.96 —— 光靠运气就会出现
        约 1 个「显著」结果。
        """
        n = max(self.n_hypotheses, 1)
        # Šidák 近似的正态分位,足够做门槛判断
        alpha = 1 - (1 - 0.05) ** (1 / n)
        return abs(_norm_ppf(alpha / 2))

    def passed(self) -> dict[str, bool]:
        """四道关,全过才算候选策略的合格证据。"""
        subs = self.subperiod_annual
        return {
            "多重检验": abs(self.t_stat) > self.bonferroni_threshold,
            "分层单调": self.monotonicity >= 0.75,
            "扣费为正": self.net_annual > 0,
            "分段稳定": bool(subs) and all(s > 0 for s in subs),
        }

    @property
    def verdict_ok(self) -> bool:
        return all(self.passed().values())


def _norm_ppf(p: float) -> float:
    """标准正态分位数(Acklam 有理逼近,精度 ~1e-9,够用且无需 scipy)。"""
    import math
    if not 0 < p < 1:
        raise ValueError("p 必须在 (0,1)")
    a = [-3.969683028665376e+01, 2.209460984245205e+02, -2.759285104469687e+02,
         1.383577518672690e+02, -3.066479806614716e+01, 2.506628277459239e+00]
    b = [-5.447609879822406e+01, 1.615858368580409e+02, -1.556989798598866e+02,
         6.680131188771972e+01, -1.328068155288572e+01]
    c = [-7.784894002430293e-03, -3.223964580411365e-01, -2.400758277161838e+00,
         -2.549732539343734e+00, 4.374664141464968e+00, 2.938163982698783e+00]
    d = [7.784695709041462e-03, 3.224671290700398e-01, 2.445134137142996e+00,
         3.754408661907416e+00]
    pl, ph = 0.02425, 1 - 0.02425
    if p < pl:
        q = math.sqrt(-2 * math.log(p))
        return (((((c[0]*q+c[1])*q+c[2])*q+c[3])*q+c[4])*q+c[5]) / \
               ((((d[0]*q+d[1])*q+d[2])*q+d[3])*q+1)
    if p > ph:
        q = math.sqrt(-2 * math.log(1 - p))
        return -(((((c[0]*q+c[1])*q+c[2])*q+c[3])*q+c[4])*q+c[5]) / \
                ((((d[0]*q+d[1])*q+d[2])*q+d[3])*q+1)
    q = p - 0.5
    r = q * q
    return (((((a[0]*r+a[1])*r+a[2])*r+a[3])*r+a[4])*r+a[5])*q / \
           (((((b[0]*r+b[1])*r+b[2])*r+b[3])*r+b[4])*r+1)
