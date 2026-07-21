"""策略信号契约。

一个策略的核心就是:给定一段收盘价,输出每个 bar 的目标仓位
(0=空仓, 1=满仓多头, -1=满仓空头,可取中间值)。
回测层拿到这串目标仓位,配合手续费/滑点算出收益。

把「信号」和「回测」解耦:策略只关心逻辑,不关心撮合;
回测只关心撮合,不关心逻辑。
"""

from __future__ import annotations

from abc import ABC, abstractmethod

import numpy as np

from qsf.core.schema import StrategyProposal


class Strategy(ABC):
    """所有可回测策略的基类。"""

    def __init__(self, proposal: StrategyProposal) -> None:
        self.proposal = proposal
        self.params = proposal.params

    @abstractmethod
    def signals(self, closes: np.ndarray) -> np.ndarray:
        """返回与 closes 等长的目标仓位数组,取值范围 [-1, 1]。

        约定:第 t 个仓位是「看到第 t 根收盘后、用于持有到 t+1」的目标仓位。
        回测层会自动做一根 bar 的滞后,避免用未来信息。
        """
        ...

    def _param(self, name: str, default: float) -> float:
        return float(self.params.get(name, default))
