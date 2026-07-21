"""特征提取层:把行情/新闻/财报浓缩成 `FeatureSet`。

- 行情类:年化波动率、动量、趋势强度。
- 情绪类:情绪分、关键词、黑天鹅强度(基于关键词命中的负面新闻)。
- 基本面类:从财报映射几个关键比率。
"""

from qsf.data.features.extract import extract_features

__all__ = ["extract_features"]
