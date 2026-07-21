"""数据层:采集 → 标准化 → 特征提取。

对外只暴露 `DataProvider` 协议 + `MockDataProvider` 默认实现,
上层拿到的永远是一个 `MarketSnapshot`,不关心数据从哪来。
"""
