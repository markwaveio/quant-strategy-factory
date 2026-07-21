"""标准化层:把各源的原始数据清洗成统一结构。

mock 采集器产出的已经是 schema 对象,这里主要做:
- 行情:按时间排序、去重、剔除非法值。
- 新闻:去重、按时间排序。
真实源接入后,清洗逻辑集中在这里,上层无感。
"""

from qsf.data.normalize.clean import normalize_news, normalize_prices

__all__ = ["normalize_prices", "normalize_news"]
