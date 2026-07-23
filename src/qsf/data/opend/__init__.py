"""富途 OpenD 数据源 —— 工厂的**统一数据底座**。

为什么统一到 OpenD:
    此前数据源混乱(QuantDinger 用腾讯财经+akshare、富途策略编辑器用自有源、
    smoke 引擎吃什么都行),导致三引擎交叉验证出现无法解释的分歧。

    既然执行场所是富途,**研究数据 = 执行数据**才能消除研究-执行落差。

相比 QuantDinger Agent Gateway 的优势(全部实测):
    · 历史 20 年(vs 8 年)
    · 港/美/沪/深四市场全通(QD 的 A 股默认关闭)
    · 前复权 QFQ 可选
    · **带 PE 与换手率** —— 解锁价值/流动性因子,这是 QD agent 通道给不了的
    · 0.1 秒/只

硬约束:
    **历史K线额度 100 只不同股票 / 30 天**(同一只重复拉不重复计)。
    要扩到几百只需要规划轮换。速率另有限制:每 30 秒最多 60 次请求。
"""

from qsf.data.opend.client import OpenDClient, OpenDError
from qsf.data.opend.provider import OpenDDataProvider

__all__ = ["OpenDClient", "OpenDError", "OpenDDataProvider"]
