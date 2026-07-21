"""评估层:用 AI 挑战前面生成的策略。

三个角色,各盯一个死角:
- 质疑者(Skeptic):过拟合、参数拍脑袋。
- 风险官(RiskOfficer):最坏能亏多少,超没超出承受范围。
- 综合评估官(Committee):像投资委员会一样综合裁决。

核心是 `LLMJudge` 抽象接口:默认 `MockLLMJudge`(离线、可复现),
后续把 key 填上就能换成 `ClaudeLLMJudge`,上层完全无感。
"""

from qsf.evaluation.committee import EvaluationBoard
from qsf.evaluation.judge import ClaudeLLMJudge, LLMJudge, MockLLMJudge

__all__ = ["EvaluationBoard", "LLMJudge", "MockLLMJudge", "ClaudeLLMJudge"]
