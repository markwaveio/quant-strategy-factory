"""QuantDinger 数据接入:把本地量化平台(127.0.0.1:15000)作为真实数据源。

对外暴露 `QuantDingerDataProvider`,满足 `qsf.data.provider.DataProvider` 协议,
因此可以直接替换 `MockDataProvider` —— 上层 8 层逻辑一个字都不用改。

  from qsf.data.quantdinger import QuantDingerDataProvider

凭证从环境变量 QD_AGENT_TOKEN 读取,绝不入库。
"""

from qsf.data.quantdinger.provider import QuantDingerDataProvider

__all__ = ["QuantDingerDataProvider"]
