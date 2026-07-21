"""记忆层:沉淀「哪些策略成功过、哪些失败过」。

用 JSONL 文件做轻量持久化(一行一条经验),支持:
- remember():把一次评估+回测的结果写成一条 MemoryRecord。
- all() / query():读取历史,供评估层参考、供决策层引用。

真实场景可换成 SQLite / 向量库,接口不变。
"""

from qsf.memory.store import MemoryStore

__all__ = ["MemoryStore"]
