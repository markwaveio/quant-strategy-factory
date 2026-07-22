---
name: market-insight
description: 采集某标的的行情与财报(可选新闻),计算特征并产出结构化洞察结论。当用户要求"分析某只票/看看某标的机会/给我一份洞察"时使用。输出对齐量化策略工厂的 MarketSnapshot + Insight schema,可直接喂给下游策略生成层。
---

# 市场洞察采集与分析

把「原始市场信息」加工成「结构化洞察」——这是量化策略工厂 ①数据层 + ②洞察层 的 agent 实现。

**核心约束:输出必须能被工厂消费。** 算法口径与 `src/qsf/data/features/extract.py`、阈值与 `src/qsf/insight/engine.py` 完全一致,保证 agent 模式与代码模式产出可比。

---

## 铁律:绝不编造数据

金融数据错一个数字,下游的策略、回测、决策全部污染。

- **只报告工具真实返回的数字。** 不确定就标 `null`,不要填"大概"。
- **财报数据尤其危险** —— 不要凭记忆写 PE/ROE/营收。没查到就写"未获取"。
- 每个数字都要能说出**来自哪次工具调用**。
- 数据不足时,宁可产出更少的洞察,也不要为了凑数而推测。

---

## 步骤 1 · 确定标的与市场

标的代码要带市场归属,否则拉不到数据:

| 市场 | `market` 值 | 代码格式 | 示例 |
|---|---|---|---|
| 港股 | `HKStock` | 5 位数字 | `00700` 腾讯 |
| A股 | `CNStock` | 6 位数字 | `600519` 茅台 |
| 美股 | `USStock` | 字母代码 | `AAPL` |
| 加密 | `Crypto` | 交易对 | `BTCUSDT` |

不确定代码时先用 `search_symbols` 查:

```
search_symbols(market="HKStock", q="腾讯")
```

---

## 步骤 2 · 拉行情

用 QuantDinger MCP:

```
get_klines(market="HKStock", symbol="00700", timeframe="1d", limit=120)
```

**`limit` 至少 60**,否则算不出结构切换(需要 60 根)。推荐 120~250。

拿到后先自检:
- `count` 是否 ≈ 请求的 limit?差太多说明数据不全,要在结论里说明
- 最后一根的时间戳是不是最近交易日?过旧要提示
- `count == 0` → **停止,不要继续分析**。报告"该标的无行情数据",并说明可能原因(代码错误/市场未开放/数据源不可达)

---

## 步骤 3 · 拉财报(尽力而为)

⚠️ **QuantDinger 的 Agent Gateway 不暴露财报端点** —— `get_klines`/`get_price` 之外没有 fundamentals 工具。所以财报要走替代路径:

1. **优先**:如果环境里有其它财务数据 MCP/工具,用它
2. **其次**:用 WebSearch 搜"<公司名> 最新财报 市盈率 ROE",从**可信来源**(交易所公告、主流财经站)提取
3. **兜底**:标记为未获取

**无论走哪条,都必须标注来源。** 格式:

```
fundamentals: { "pe": 18.5, "roe": 0.21, "_source": "WebSearch: 东方财富 2026-07-20" }
```

搜不到就是搜不到:

```
fundamentals: {}
fundamentals_note: "未获取到可信财报数据,本次洞察仅基于行情"
```

**绝对不要凭印象填数字。**

---

## 步骤 4 · 计算特征

用行情收盘价序列计算。**口径必须与工厂代码一致**:

| 特征 | 算法 | 说明 |
|---|---|---|
| `volatility` | `std(日收益率, ddof=1) × √252` | 年化波动率 |
| `momentum` | `closes[-1] / closes[-21] - 1` | 20 日动量 |
| `trend_strength` | 收盘价对时间做线性回归,`斜率 ÷ 均价 × 20`,裁剪到 [-1, 1] | 趋势强度 |
| `sentiment` | 新闻正负词占比,`(正-负)/(正+负)`,范围 [-1, 1] | 无新闻则 `null` |
| `black_swan_score` | 命中黑天鹅词的新闻数 ÷ 总新闻数,范围 [0, 1] | 无新闻则 `0.0` |

日收益率 = `(close[i] - close[i-1]) / close[i-1]`

**算术要自己动手核对**——可以用 `python3 -c` 或代码执行工具验算,不要心算长序列。

---

## 步骤 5 · 生成洞察

严格按阈值判定。**每条洞察必须带量化证据**,不能只有形容词。

| 类型 `kind` | 触发条件 | score 计算 |
|---|---|---|
| `strength` | `trend_strength ≥ 0.15` **且** `momentum ≥ 0.05` | `min(1, trend + momentum×2)` |
| `weakness` | `trend_strength ≤ -0.15` **且** `momentum ≤ -0.05` | `min(1, |trend| + |momentum|×2)` |
| `anomaly` | 近 5 日收益均值的 z-score,`|z| ≥ 2.0` | `min(1, |z| / 4)` |
| `regime_shift` | 近 20 日波动 ÷ 前 40 日波动 `≥ 1.5` | `min(1, (ratio-1) / 2)` |
| `event` | `black_swan_score ≥ 0.3` 或 `|sentiment| ≥ 0.5` | 见下 |

z-score 算法:`z = (近5日收益均值 - 全期均值) / (全期标准差 / √5)`

事件类 score:黑天鹅取 `min(1, black_swan + 0.3)`;极端情绪取 `min(1, |sentiment|)`

**没有任何条件触发是完全正常的结果** —— 说明该标的当前无显著信号,如实报告即可,不要为了"有产出"而降低标准。

---

## 步骤 6 · 输出

先给**人类可读的结论**,再给**结构化 JSON**。

### 人类可读部分

```markdown
## 腾讯控股 (HKStock 00700) · 洞察结论

**一句话**:近 20 日趋势走弱,波动率抬升至 0.31,无黑天鹅信号。

**特征**
| 指标 | 数值 | 读法 |
|---|---|---|
| 年化波动率 | 0.31 | 中等偏高 |
| 20日动量 | -6.2% | 下行 |
| 趋势强度 | -0.18 | 温和下行趋势 |

**洞察**
- [weakness · 0.30] 走弱:趋势强度 -0.18、20日动量 -6.2%
- [regime_shift · 0.28] 波动率抬升:近端/远端比 1.56,市场结构可能切换

**数据说明**:行情 120 根日线(至 2026-07-21,QuantDinger MCP)。财报未获取。
```

### 结构化部分

对齐工厂 schema,可直接落盘给下游:

```json
{
  "as_of": "2026-07-22T10:30:00",
  "symbol": "00700",
  "market": "HKStock",
  "features": {
    "symbol": "00700",
    "as_of": "2026-07-22T10:30:00",
    "volatility": 0.31,
    "momentum": -0.062,
    "trend_strength": -0.18,
    "sentiment": null,
    "keywords": [],
    "black_swan_score": 0.0,
    "fundamentals": {}
  },
  "insights": [
    {
      "kind": "weakness",
      "symbol": "00700",
      "as_of": "2026-07-22T10:30:00",
      "score": 0.30,
      "summary": "00700 走弱:趋势强度 -0.18、20日动量 -6.2%",
      "evidence": { "trend_strength": -0.18, "momentum": -0.062 }
    }
  ],
  "data_quality": {
    "bars_received": 120,
    "latest_bar": "2026-07-21",
    "fundamentals_source": null,
    "news_count": 0
  }
}
```

`kind` 只能取:`anomaly` / `strength` / `weakness` / `regime_shift` / `event`(与工厂 `InsightKind` 枚举一致)。

---

## 多标的批量

逐个走完步骤 1–5,最后合并输出。**按 score 最高的洞察降序排列标的**,让最值得注意的排在前面。

单个标的失败(无数据/代码错误)不要中断整批 —— 跳过它并在 `data_quality` 里说明。

---

## 边界

这个 skill 只负责**采集与洞察**,不做以下事情:

- ❌ 不生成策略(那是③策略生成层)
- ❌ 不做买卖建议(那是⑦决策层,且必须先过④评估层的三角色评审)
- ❌ 不下单

洞察是**观察**,不是**结论**。"波动率抬升"是观察;"应该买入"不是这个 skill 该说的话。

---

## 与工厂代码的关系

| 本 skill | 对应工厂代码 |
|---|---|
| 步骤 2–3 采集 | `src/qsf/data/provider.py` 的 `DataProvider` 实现 |
| 步骤 4 特征 | `src/qsf/data/features/extract.py` |
| 步骤 5 洞察 | `src/qsf/insight/engine.py` |
| 步骤 6 输出 | `src/qsf/core/schema.py` 的 `FeatureSet` / `Insight` |

改阈值或算法时**两边要同步**,否则 agent 模式与代码模式会产出不一致的结果。
