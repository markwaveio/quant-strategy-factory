#!/usr/bin/env python3
"""把工厂产出的策略卡导出成【富途牛牛策略编辑器】可直接粘贴的代码。

    python3 scripts/export_futu_strategy.py --fast 8 --slow 40
    python3 scripts/export_futu_strategy.py --fast 8 --slow 40 -o /tmp/futu.py

为什么需要它:
    富途策略编辑器有独立的回测引擎和数据源。把同一策略同时跑在
    工厂 smoke / QuantDinger / 富途 三个引擎上,是**交叉验证**——
    结论一致则策略卡可信度大增;差异大则说明某一环建模有问题,
    这比只信单一引擎有价值得多。

富途 API 要点(实测自用户的双均线策略):
    declare_trig_symbol()                     声明驱动标的
    show_variable(默认值, GlobalType.INT)     声明可在 UI 调节的参数
    position_holding_qty(symbol=...)          查持仓数量
    ma(symbol=, period=, bar_type=BarType.K_DAY,
       data_type=DataType.CLOSE, select=2,
       session_type=THType.RTH)               均线

    ⚠️ 四个【约定函数】缺一不可,即使函数体为空:
        initialize / trigger_symbols / custom_indicator / global_variables
        少写 custom_indicator 会报「缺少 custom_indicator() 约定函数」。(实测踩过)

    ⚠️ 富途的模式是「持仓状态 + 当前形态」,不是边缘触发:
        持仓==0 且 快线>慢线 → 买入
        持仓>0  且 快线<慢线 → 卖出
    效果等价于金叉/死叉,但更稳(不会因错过某一根 K 线而漏信号)。
"""

from __future__ import annotations

import argparse
import sys

# --------------------------------------------------------------------------- #
# 下单 API(实测自用户的富途策略)
#
#   place_limit(symbol=, price=, qty=, side=OrderSide.SELL|BUY,
#               time_in_force=TimeInForce.DAY,
#               order_trade_session_type=TSType.RTH)
#   max_qty_to_sell(symbol=)   最大可卖数量
#   lot_size(symbol=)          每手股数
#   bid(symbol=, level=N)      买盘第 N 档   —— 卖出时用(打买盘,确保成交)
#   ask(symbol=, level=N)      卖盘第 N 档   —— 买入时用(打卖盘)
#
# ✅ 卖出路径 100% 实测确认。
# ⚠️ 买入路径中的 ask() / max_qty_to_buy() 是按对称性推断的:
#    卖出打 bid(买盘)、买入自然打 ask(卖盘)。若富途函数名不同,
#    编辑器底部会立刻报错(它有语法校验),按提示改这两个名字即可。
#
# 安全模式(取自用户代码):下单前先校验可交易数量 >= 一手。
# --------------------------------------------------------------------------- #

TEMPLATE = '''# ============================================================
# {title}
# 由「量化策略工厂」自动生成 —— {source}
# ------------------------------------------------------------
# 参数来源: 网格搜索 {space}
#           稳健性筛选(覆盖率 > 最差 > 均值), 非人工指定
# 标的池:   {pool}
# ⚠️ 工厂评审结论: {verdict}
#    {verdict_note}
# 仅供研究, 非投资建议。
# ============================================================

class Strategy(StrategyBase):

    def initialize(self):
        declare_strategy_type(AlgoStrategyType.SECURITY)
        self.trigger_symbols()
        self.custom_indicator()
        self.global_variables()
        print("{title} 初始化完成 | 快线={{}} 慢线={{}}".format(
            self.均线快线周期, self.均线慢线周期))

    def trigger_symbols(self):
        """定义驱动标的:回测时在运行设置里选具体股票。"""
        self.驱动标的1 = declare_trig_symbol()

    def custom_indicator(self):
        """约定函数 —— 必须存在,否则编辑器报「缺少 custom_indicator() 约定函数」。

        本策略直接用内置 ma() 计算均线,无需注册自定义指标,故留空。
        """
        pass

    def global_variables(self):
        """可在界面上调节的参数。默认值来自工厂的网格搜索结果。"""
        self.均线快线周期 = show_variable({fast}, GlobalType.INT)
        self.均线慢线周期 = show_variable({slow}, GlobalType.INT)
        self.开仓数量     = show_variable({qty}, GlobalType.INT)

    # --- 条件判断 ------------------------------------------------------- #

    def _持仓为空(self):
        return position_holding_qty(symbol=self.驱动标的1) == 0

    def _快线在上(self):
        """快线 > 慢线 —— 多头形态。"""
        fast = ma(symbol=self.驱动标的1, period=int(self.均线快线周期),
                  bar_type=BarType.K_DAY, data_type=DataType.CLOSE,
                  select=2, session_type=THType.RTH)
        slow = ma(symbol=self.驱动标的1, period=int(self.均线慢线周期),
                  bar_type=BarType.K_DAY, data_type=DataType.CLOSE,
                  select=2, session_type=THType.RTH)
        return fast > slow

    def _快线在下(self):
        """快线 < 慢线 —— 空头形态。"""
        fast = ma(symbol=self.驱动标的1, period=int(self.均线快线周期),
                  bar_type=BarType.K_DAY, data_type=DataType.CLOSE,
                  select=2, session_type=THType.RTH)
        slow = ma(symbol=self.驱动标的1, period=int(self.均线慢线周期),
                  bar_type=BarType.K_DAY, data_type=DataType.CLOSE,
                  select=2, session_type=THType.RTH)
        return fast < slow

    # --- 主逻辑 --------------------------------------------------------- #

    def handle_data(self):
        """每根 K 线执行一次。

        富途模式:「持仓状态 + 当前形态」,而非边缘触发的金叉/死叉。
        效果等价但更稳 —— 不会因错过某一根 K 线而漏掉信号。
        """
        if self._持仓为空():
            if self._快线在上():
                self._买入()
        else:
            if self._快线在下():
                self._卖出()

    # --- 下单 ----------------------------------------------------------- #

    def _买入(self):
        """限价买入。打卖盘第 5 档以确保成交;数量按整手取。"""
        每手 = lot_size(symbol=self.驱动标的1)
        数量 = int(self.开仓数量) * 每手
        if 数量 < 每手:
            print("买入数量不足一手, 跳过")
            return
        价格 = ask(symbol=self.驱动标的1, level=5)
        place_limit(symbol=self.驱动标的1, price=价格, qty=数量,
                    side=OrderSide.BUY, time_in_force=TimeInForce.DAY,
                    order_trade_session_type=TSType.RTH)
        print("买入 {{}} 股 @ {{}}".format(数量, 价格))

    def _卖出(self):
        """限价清仓。打买盘第 5 档以确保成交。"""
        可卖 = max_qty_to_sell(symbol=self.驱动标的1)
        每手 = lot_size(symbol=self.驱动标的1)
        if 可卖 < 每手:
            print("可卖数量不足一手, 跳过")
            return
        价格 = bid(symbol=self.驱动标的1, level=5)
        place_limit(symbol=self.驱动标的1, price=价格, qty=1.00 * 可卖,
                    side=OrderSide.SELL, time_in_force=TimeInForce.DAY,
                    order_trade_session_type=TSType.RTH)
        print("清仓 {{}} 股 @ {{}}".format(可卖, 价格))
'''


def render(fast: int, slow: int, qty: int, title: str, pool: str,
           space: str, source: str, verdict: str, verdict_note: str) -> str:
    return TEMPLATE.format(
        title=title, fast=fast, slow=slow, qty=qty, pool=pool, space=space,
        source=source, verdict=verdict, verdict_note=verdict_note,
    )


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="导出富途策略编辑器代码")
    p.add_argument("--fast", type=int, default=8, help="快线周期(默认取自搜索结果)")
    p.add_argument("--slow", type=int, default=40, help="慢线周期")
    p.add_argument("--qty", type=int, default=1, help="开仓手数")
    p.add_argument("--title", default="港股科技趋势跟随 v1")
    p.add_argument("--pool", default="00700 / 09988 / 03690 / 01810")
    p.add_argument("--space", default="fast[5,8,10,15] x slow[20,30,40,60] = 16组 x 4标的")
    p.add_argument("--source", default="策略卡 strategy-card-hk-tech-trend-v1")
    p.add_argument("--verdict", default="REJECT (评分 0.337)")
    p.add_argument(
        "--verdict-note",
        default="池级最深回撤 -41.1% 超 25% 红线;4/4 标的盈利但风控不达标。"
                "\n#    放进富途回测的目的是【交叉验证】,不是准备实盘。",
    )
    p.add_argument("-o", "--output", help="写入文件(默认打印到 stdout)")
    a = p.parse_args(argv)

    if a.fast >= a.slow:
        print("❌ 快线周期必须小于慢线周期", file=sys.stderr)
        return 1

    code = render(a.fast, a.slow, a.qty, a.title, a.pool,
                  a.space, a.source, a.verdict, a.verdict_note)

    if a.output:
        with open(a.output, "w", encoding="utf-8") as f:
            f.write(code)
        print(f"✅ 已写入 {a.output}", file=sys.stderr)
    else:
        print(code)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
