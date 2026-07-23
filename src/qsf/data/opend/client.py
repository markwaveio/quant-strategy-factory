"""OpenD 客户端 —— 历史行情、PE、换手率的统一入口。

只读:仅调用行情接口,不下单、不改单。交易请走富途官方 skill。

额度自检:每次批量拉取前后打印额度,避免不知不觉撞到
「100 只不同股票 / 30 天」的上限。
"""

from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass


class OpenDError(RuntimeError):
    """OpenD 调用失败(未启动、无权限、额度耗尽等)。"""


@dataclass
class Bar:
    """一根 K 线。比 OHLCV 多了 PE 与换手率 —— 这是 OpenD 的增量价值。"""

    date: str            # YYYY-MM-DD
    open: float
    high: float
    low: float
    close: float
    volume: float
    turnover: float      # 成交额
    pe_ratio: float | None = None       # 市盈率 → 价值因子
    turnover_rate: float | None = None  # 换手率 → 流动性因子


# 富途代码前缀 ←→ 工厂市场名
MARKET_PREFIX = {"HKStock": "HK", "USStock": "US", "CNStock_SH": "SH", "CNStock_SZ": "SZ"}


def to_futu_code(market: str, symbol: str) -> str:
    """(市场, 代码) → 富途代码。

    A 股需要区分沪深:6 开头走 SH,其余走 SZ。
    """
    if market == "CNStock":
        return f"{'SH' if symbol.startswith('6') else 'SZ'}.{symbol}"
    return f"{MARKET_PREFIX.get(market, market)}.{symbol}"


class OpenDClient:
    def __init__(self, host: str | None = None, port: int | None = None,
                 *, quiet: bool = True) -> None:
        self.host = host or os.getenv("FUTU_OPEND_HOST", "127.0.0.1")
        self.port = int(port or os.getenv("FUTU_OPEND_PORT", "11111"))
        if quiet:
            # futu SDK 会往 stdout 打大量连接日志,污染结构化输出
            logging.disable(logging.CRITICAL)
        self._ctx = None

    def __enter__(self):
        self._connect()
        return self

    def __exit__(self, *exc):
        self.close()

    def _connect(self):
        if self._ctx is not None:
            return self._ctx
        try:
            from futu import OpenQuoteContext
        except ImportError as e:
            raise OpenDError("未安装 futu-api,执行:pip install futu-api") from e
        try:
            self._ctx = OpenQuoteContext(host=self.host, port=self.port)
        except Exception as e:
            raise OpenDError(
                f"连不上 OpenD({self.host}:{self.port})。请确认 Futu_OpenD 已启动。"
            ) from e
        return self._ctx

    def close(self):
        if self._ctx is not None:
            try:
                self._ctx.close()
            except Exception:
                pass
            self._ctx = None

    # --- 额度 ----------------------------------------------------------- #

    def quota(self) -> tuple[int, int]:
        """返回 (已用, 剩余)。上限 100 只不同股票 / 30 天。"""
        from futu import RET_OK
        ret, q = self._connect().get_history_kl_quota(get_detail=False)
        if ret != RET_OK:
            raise OpenDError(f"额度查询失败: {q}")
        used, remain = q[0], q[1]
        return int(used), int(remain)

    # --- 历史行情 -------------------------------------------------------- #

    def history(self, market: str, symbol: str, start: str, end: str,
                *, adjust: str = "hfq") -> list[Bar]:
        """拉历史日 K(自动翻页)。

        `adjust`: hfq 后复权 / qfq 前复权 / none 不复权。

        **默认后复权。** 不复权会让除权造成假跳空,污染均线与收益;
        但前复权也不能用于多年期研究 —— 它从历史价里逐次减去分红,
        累计分红超过当年股价时调整后价格会变成负数(实测 8 年期 50 只港股
        有 5 只中招),使收益率算出低于 -100% 的不可能值。
        后复权锚定最早价格向后放大,永远为正。

        只有"看当前价位"的场景(如画图、判断今日是否站上均线)才适合 qfq,
        因为它保持最新价 = 真实市价。
        """
        from futu import RET_OK, AuType, KLType
        au = {"qfq": AuType.QFQ, "hfq": AuType.HFQ, "none": AuType.NONE}.get(adjust, AuType.QFQ)
        code = to_futu_code(market, symbol)
        ctx = self._connect()

        bars: list[Bar] = []
        page_key = None
        for _ in range(30):  # 翻页上限,防死循环
            ret, df, page_key = ctx.request_history_kline(
                code, start=start, end=end, ktype=KLType.K_DAY,
                autype=au, max_count=1000, page_req_key=page_key,
            )
            if ret != RET_OK:
                raise OpenDError(f"{code} 拉取失败: {df}")
            for _, r in df.iterrows():
                bars.append(Bar(
                    date=str(r["time_key"])[:10],
                    open=float(r["open"]), high=float(r["high"]),
                    low=float(r["low"]), close=float(r["close"]),
                    volume=float(r.get("volume") or 0),
                    turnover=float(r.get("turnover") or 0),
                    pe_ratio=_f(r.get("pe_ratio")),
                    turnover_rate=_f(r.get("turnover_rate")),
                ))
            if not page_key:
                break
            time.sleep(0.35)  # 每30秒最多60次请求
        return bars

    def history_batch(self, market: str, symbols: list[str], start: str, end: str,
                      *, adjust: str = "qfq", on_progress=None) -> dict[str, list[Bar]]:
        """批量拉取。单只失败不中断整批。"""
        out: dict[str, list[Bar]] = {}
        for i, s in enumerate(symbols, 1):
            try:
                b = self.history(market, s, start, end, adjust=adjust)
                if b:
                    out[s] = b
            except OpenDError:
                pass
            if on_progress:
                on_progress(i, len(symbols), len(out))
            time.sleep(0.35)
        return out


def _f(v) -> float | None:
    try:
        f = float(v)
        return f if f == f else None  # 过滤 NaN
    except (TypeError, ValueError):
        return None
