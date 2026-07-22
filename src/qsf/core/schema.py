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
