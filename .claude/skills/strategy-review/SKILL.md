---
name: strategy-review
description: 把洞察变成经过对抗式评审的决策。生成策略代码 → QuantDinger 真实回测 → 质疑者/风险官/投委会三角色挑战 → 出决策。当用户说"给这个标的做个策略/回测一下/评审一下这个策略/该不该买"时使用。覆盖量化策略工厂的 ③策略生成 → ⑥回测 → ④评估 → ⑦决策。
---

# 策略生成与对抗式评审

接在 [daily-scan](../daily-scan/SKILL.md) / [market-insight](../market-insight/SKILL.md) 之后 —— 它们产出**观察**,本 skill 产出**决策**。

这是本工厂**唯一的差异化所在**:QuantDinger 的 AI 是多模型同质投票(降方差),我们是**异质角色对抗制衡**(降系统性盲区)。三个角色必须真的互相挑战,不能走过场。

---

## 铁律

1. **回测数字只能来自 QuantDinger 的真实返回** —— 绝不估算、绝不"大概"。
2. **三个角色必须独立表态**,即使结论相同也要各自给出理由。不许合并成一句"综合来看…"。
3. **风险官对超红线回撤有一票否决权** —— 其它角色再看好也否决。
4. **决策是建议,不是指令。** 永远不下单。

---

## 步骤 1 · 确定评审对象

需要三样:**标的** + **策略族** + **回测区间**。

策略族按洞察选(与工厂 `StrategyFamily` 一致):

| 洞察 | 选哪族 | 理由 |
|---|---|---|
| `strength` / 正动量 | **趋势跟随** | 顺势 |
| `regime_shift` / 高波动 | **均值回归** | 赚震荡 |
| `event` / 黑天鹅 | **新闻事件** | 事件驱动 |
| 无洞察 | 趋势(基线) | 给个基准供评审 |

回测区间默认**近 1 年**(`end=今天`, `start=今天-365天`)。

---

## 步骤 2 · 写指标代码

⚠️ **先调 `get_indicator_authoring_contract` 拿最新契约**,别凭本文档硬写 —— 契约会变。

### 硬性要求(契约 v1)

```
必需全局:  my_indicator_name, my_indicator_description
必需起手:  df = df.copy()
四路信号:  df['open_long'] / df['close_long'] / df['open_short'] / df['close_short']
必需输出:  output = {'name', 'plots', 'signals'}   列表长度 == len(df)
参数读取:  必须 params.get('name', default)
```

**禁止**:
- ✗ `code` 字段里写自然语言(必须是可执行 Python)
- ✗ `import os/sys/requests/subprocess`
- ✗ 在 `np.where` 输出上直接链式 `.rolling`/`.shift`(要先 `pd.Series(..., index=df.index)`)
- ✗ 用旧版 `df['buy']` / `df['sell']`

### 风控注解

```python
# @strategy entryPct 0.25          # 单笔投入资金比例 0-1
# @strategy stopLossPct 0.03       # 3% 止损
# @strategy takeProfitPct 0.06
# @strategy tradeDirection long    # long / short / both
```

### 三族模板

**趋势跟随**(已实测通过):

```python
my_indicator_name = "QSF 趋势跟随"
my_indicator_description = "双均线金叉开多、死叉平多"
# @param fast_period int 10 快线周期
# @param slow_period int 30 慢线周期
# @strategy entryPct 0.25
# @strategy stopLossPct 0.03
# @strategy tradeDirection long
fast_period = int(params.get("fast_period", 10))
slow_period = int(params.get("slow_period", 30))
df = df.copy()
fast = df["close"].rolling(fast_period).mean()
slow = df["close"].rolling(slow_period).mean()
df["open_long"]  = ((fast > slow) & (fast.shift(1) <= slow.shift(1))).fillna(False).astype(bool)
df["close_long"] = ((fast < slow) & (fast.shift(1) >= slow.shift(1))).fillna(False).astype(bool)
df["open_short"] = False
df["close_short"] = False
output = {"name": my_indicator_name, "plots": [], "signals": []}
```

**均值回归**:

```python
my_indicator_name = "QSF 均值回归"
my_indicator_description = "价格偏离均线 N 倍标准差时反向押注回归"
# @param window int 20 均线窗口
# @param entry_z float 1.0 入场 z-score 阈值
# @strategy entryPct 0.2
# @strategy stopLossPct 0.04
# @strategy tradeDirection long
window = int(params.get("window", 20))
entry_z = float(params.get("entry_z", 1.0))
df = df.copy()
ma = df["close"].rolling(window).mean()
sd = df["close"].rolling(window).std()
z = (df["close"] - ma) / sd.replace(0, float("nan"))
df["open_long"]  = ((z <= -entry_z) & (z.shift(1) > -entry_z)).fillna(False).astype(bool)
df["close_long"] = ((z >= 0) & (z.shift(1) < 0)).fillna(False).astype(bool)
df["open_short"] = False
df["close_short"] = False
output = {"name": my_indicator_name, "plots": [], "signals": []}
```

**新闻事件**:事件位置需由调用方以参数传入(agent 从新闻推断日期偏移),
在冲击后 N 根 K 线内开多博反弹,持有 M 根后平仓。无明确事件位置时**不要硬造**,
改用趋势基线并在报告里说明。

---

## 步骤 3 · 沙箱校验(必做)

```
validate_indicator_code(code=..., market=..., symbol=...)
```

看 `data.success`:

- `true` → 继续。留意 `hints`(如 `NO_TAKE_PROFIT`、`EMPTY_PLOTS_AND_SIGNALS`,多为 info 级)
- `false` → 读 `msg` / `error_type` / `details` 修代码,**修好再往下走**

**永远不要跳过校验直接回测** —— 校验免费且快,回测是排队任务。

---

## 步骤 4 · 真实回测

```
submit_backtest(code=..., market=..., symbol=..., timeframe="1d",
                start_date="YYYY-MM-DD", end_date="YYYY-MM-DD")
```

> 🚨 **`# @strategy stopLossPct` 注解在 agent 回测端点【完全不生效】。**
>
> 根因:`/api/agent/v1/backtests` 只从 payload 读 `strategy_config`
> (`routes/agent_v1/backtests.py:69`),**从不解析代码里的 `@strategy` 注解** ——
> 那个解析器只在指标 IDE 那条路径上被调用。
>
> **必须显式传 payload**:
> ```json
> "strategy_config": {"risk": {"stopLossPct": 0.03, "takeProfitPct": 0.06}}
> ```
>
> **实测对照**(腾讯00700, fast8/slow40, 2年):
> | 写法 | 收益 | 回撤 |
> |---|---|---|
> | 代码注解 3% | 15.39% | −25.90% |
> | **payload 3%** | **31.44%** | **−19.65%** |
>
> 差距巨大 —— 收益翻倍、回撤从超红线变为达标。
> **不传 payload 的回测,其风控参数全是摆设,回撤数字会系统性偏高。**

> 🚨 **`start_date` 和 `end_date` 是必填的。** 漏了任务会 `failed`,
> 报错 `start_date and end_date are required (YYYY-MM-DD)`。这是实测踩过的坑。

拿到 `job_id` 后用 `wait_for_job` 或轮询 `get_job`。**终态**:`succeeded` / `failed`。
失败时读 `error` 字段(含完整 traceback)。

### 结果字段

| 字段 | 含义 | 注意 |
|---|---|---|
| `totalReturn` | 总收益 % | **是百分数不是小数**,-21.92 表示 -21.92% |
| `annualReturn` | 年化收益 % | 同上 |
| `maxDrawdown` | 最大回撤 % | 负数,-22.31 表示回撤 22.31% |
| `sharpeRatio` | 夏普 | |
| `winRate` | 胜率 | ⚠️ **0-100 百分数**(50.0 = 50%),**不是** 0-1 小数 |
| `totalTrades` | 交易笔数 | **喂给质疑者的关键输入** |
| `profitFactor` | 盈亏比 | |
| `totalCommission` | 总手续费 | |
| `equityCurve` | 净值曲线 | 数组 |
| `precision_info` | 撮合精度 | 严格模式=已收盘K线确认信号、下根开盘成交(防未来函数) |

**转换成工厂 schema 时,收益类和胜率都要除以 100**:

```python
total_return  = r["totalReturn"]  / 100     # -21.92 → -0.2192
annual_return = r["annualReturn"] / 100
max_drawdown  = r["maxDrawdown"]  / 100     # -22.31 → -0.2231
win_rate      = r["winRate"]      / 100     # 50.0   → 0.50   ← 易漏!
sharpe        = r["sharpeRatio"]            # 不用换算
```

> 🚨 **`winRate` 忘了除 100 会让质疑者的胜率判定永远失效** —— `50.0 < 0.3` 恒为假,
> 低胜率策略会被静默放过。实测踩过。

---

## 步骤 5 · 三角色对抗评审

三个角色**分别独立**审同一份回测。阈值与 `src/qsf/evaluation/judge.py` 严格一致。

### 质疑者 — 盯过拟合与统计有效性

| 触发 | 意见 |
|---|---|
| `totalTrades < 10` | 「交易样本仅 N 笔,统计不显著,疑似过拟合」建议延长区间或放宽入场 |
| `winRate == 0` 且有交易 | 「N 笔交易胜率为 0,**策略逻辑在该标的上完全失效**」建议先检验假设而非调参 |
| `totalTrades ≥ 10` 且 `winRate < 0.3` | 「胜率仅 X%,盈亏比需足够高才可能成立」 |
| `sharpeRatio > 3.0` | 「夏普偏高,警惕曲线拟合」 |
| 参数极端(`>100` 或 `<0.001`) | 「参数取值可疑,像是手工凑出来的」 |

无 concern → APPROVE(信心 0.8);有 → REVISE(信心 `max(0.4, 0.8-0.1×N)`)

> 📌 **这里踩过坑**:门槛原本是 `< 5`,一个 **5 笔交易、0% 胜率**的失败策略因此被判 APPROVE。
> 门槛提到 10 并加了胜率检查。**质疑者宁可多疑,不可漏放。**

### 风险官 — 只问最坏亏多少

| 触发 | 意见 | 裁决 |
|---|---|---|
| `|maxDrawdown| > 红线` | 「超过风险红线,最坏亏损不可接受」 | **REJECT(一票否决)** |
| `|maxDrawdown| > 红线×0.8` | 「逼近红线,仓位需压缩」 | REVISE |
| `black_swan_score ≥ 0.3` | 「标的存在黑天鹅暴露,尾部风险偏高」 | 至少 REVISE |

红线默认 25%(`configs/*.yaml` 的 `max_drawdown_limit`)。建议永远包含「设置止损」「按波动率缩放仓位」。

### 投委会 — 综合裁决

| 条件 | 裁决 |
|---|---|
| `sharpe ≥ 0.5` 且 `收益 > 0` 且 `|回撤| < 红线` 且无历史失败警示 | APPROVE |
| `收益 ≤ 0` 或 `sharpe < 0` | REJECT「风险调整后收益不佳」 |
| 其余 | REVISE |

记忆层若显示**同族同标的历史失败 ≥ 2 次**,必须提出并倾向保守。

### 聚合

```
风险官 REJECT              → 最终 REJECT(一票否决)
三角色全 APPROVE           → APPROVE
有人 REJECT 且委员会也 REJECT → REJECT
其余                       → REVISE
综合评分 = Σ(裁决分×信心) / Σ信心    approve=1 / revise=0.5 / reject=0
```

---

## 步骤 6 · 出决策

**只有 APPROVE 才产出可执行建议。** REVISE/REJECT 一律给 `avoid` 或 `hold`,并说明原因。

仓位:`基础仓位(0.2) × 综合评分 × 回撤惩罚`,回撤惩罚 = `max(0.3, 1 - |回撤|/红线)`。

### 输出格式

```markdown
## 决策 · 腾讯控股 00700 · 趋势跟随

**裁决:REJECT** (综合评分 0.32)

### 回测(QuantDinger 真实,2025-07-22 → 2026-07-22)
| 指标 | 值 |
|---|---|
| 总收益 | **-21.92%** |
| 最大回撤 | **-22.31%** |
| 夏普 | **-1.91** |
| 交易/胜率 | 5 笔 / **0%** |
| 手续费 | 91.58 |

### 三角色意见
**质疑者 → REVISE** (0.6)
- 交易样本仅 5 笔,统计不显著,疑似过拟合
- 5 笔交易胜率为 0,策略逻辑在该标的上完全失效

**风险官 → REVISE** (0.7)
- 最大回撤 22.3% 逼近红线 25%,仓位需压缩
- 建议:设置止损、按波动率缩放仓位

**投委会 → REJECT** (0.75)
- 风险调整后收益不佳(夏普/总收益为负)

### 决策
**AVOID** — 建议仓位 0%

该策略在此标的上被三角色一致否定。**注意:你当前持有此标的**(富途模拟盘 100 股,
浮亏 -4.13%)—— 本结论仅说明「双均线趋势策略不适用于腾讯」,
不等于「应该卖出持仓」。是否调整持仓需另行评估。

_本报告由自动化系统生成,仅供研究,不构成投资建议。_
```

---

## 步骤 7 · 沉淀到记忆层

把结果追加到 `data_store/memory/memory.jsonl`(工厂 ⑤记忆层格式):

```json
{"strategy_id":"...","family":"trend","symbol":"00700",
 "created_at":"...","outcome":"failure",
 "market_context":{"volatility":0.39,"trend_strength":0.13},
 "lesson":"trend 在 00700 失败:5笔交易胜率为0,策略逻辑完全失效"}
```

`outcome` 判定:REJECT 或收益<0 → `failure`;APPROVE 且夏普>0 且收益>0 → `success`;其余 `neutral`。

**下次评审同族同标的时必须先读记忆** —— 历史失败 ≥2 次要在投委会意见里点明。

---

## 边界

- ❌ **永远不下单。** 即使 token 有 T 权限、即使 `place_quick_order` 可用。
- ❌ 不改 `paper_only` 设置。
- ❌ 不因为"想给个正面结论"而放宽阈值。**REJECT 是有价值的产出。**

决策是**建议**;执行由人做。
