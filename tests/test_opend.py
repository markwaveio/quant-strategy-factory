"""OpenD 数据源测试:代码映射与契约(不依赖真实 OpenD 连接)。"""

import pytest

from qsf.core.config import DataConfig
from qsf.data.opend.client import Bar, to_futu_code
from qsf.data.opend.provider import OpenDDataProvider


def test_code_mapping_covers_four_markets():
    assert to_futu_code("HKStock", "00700") == "HK.00700"
    assert to_futu_code("USStock", "AAPL") == "US.AAPL"
    # A 股要按首位区分沪深 —— 6 开头走 SH,其余 SZ
    assert to_futu_code("CNStock", "600519") == "SH.600519"
    assert to_futu_code("CNStock", "000001") == "SZ.000001"


def test_bar_carries_pe_and_turnover_rate():
    """OpenD 相比 QuantDinger agent 通道的增量,必须在 schema 里体现。"""
    b = Bar(date="2026-07-23", open=1, high=2, low=0.5, close=1.5,
            volume=100, turnover=150, pe_ratio=16.2, turnover_rate=0.002)
    assert b.pe_ratio == 16.2
    assert b.turnover_rate == 0.002


def test_requires_explicit_symbols():
    """OpenD 没有默认标的池接口,symbols 为空必须明确报错而非静默返回空。"""
    from qsf.data.opend.client import OpenDError
    p = OpenDDataProvider(DataConfig(mode="opend", symbols=[]))
    with pytest.raises(OpenDError, match="必须显式指定"):
        p.build_snapshot()
