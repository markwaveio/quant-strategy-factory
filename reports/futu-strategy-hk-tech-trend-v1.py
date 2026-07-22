# ============================================================
# 港股科技趋势跟随 v1
# 由「量化策略工厂」自动生成 —— 策略卡 strategy-card-hk-tech-trend-v1
# ------------------------------------------------------------
# 参数来源: 网格搜索 fast[5,8,10,15] x slow[20,30,40,60] = 16组 x 4标的
#           稳健性筛选(覆盖率 > 最差 > 均值), 非人工指定
# 标的池:   00700 / 09988 / 03690 / 01810
# ⚠️ 工厂评审结论: REJECT (评分 0.337)
#    池级最深回撤 -41.1% 超 25% 红线;4/4 标的盈利但风控不达标。
#    放进富途回测的目的是【交叉验证】,不是准备实盘。
# 仅供研究, 非投资建议。
# ============================================================

class Strategy(StrategyBase):

    def initialize(self):
        declare_strategy_type(AlgoStrategyType.SECURITY)
        self.trigger_symbols()
        self.custom_indicator()
        self.global_variables()
        print("港股科技趋势跟随 v1 初始化完成 | 快线={} 慢线={}".format(
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
        self.均线快线周期 = show_variable(8, GlobalType.INT)
        self.均线慢线周期 = show_variable(40, GlobalType.INT)
        self.开仓数量     = show_variable(1, GlobalType.INT)

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
        print("买入 {} 股 @ {}".format(数量, 价格))

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
        print("清仓 {} 股 @ {}".format(可卖, 价格))
