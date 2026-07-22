# 量化策略工厂 × QuantDinger · 重叠与互补地图

> 本文结论全部**实证自源码**(`/Users/mark/Documents/Cursor/QuantDinger`),行数为实测 `wc -l`。
> QuantDinger v3.0.31 · Apache-2.0 · 调研日期 2026-07-22

---

## 核心结论

**本工厂凭直觉设计的 8 层里,有一半 QuantDinger 用生产级代码已经实现了。**

这不是坏消息,而是两个独立系统**收敛到同一套架构**的证明——反证分层思路是对的。

含义:**别重复造它已有的重活,把力气押到它的空白处。**

---

## 逐层对照

| # | 本工厂 | QuantDinger | 裁决 |
|---|---|---|---|
| ① | **数据层** — mock 采集器 + 特征 | `market_data_collector` — 10+ 加密所(CCXT)、IBKR/MT5/Alpaca、多数据 API | **它 carry** |
| ② | **洞察层** — `insight/engine.py` 158 行,异常/走强/结构切换 | `fast_analysis.py` 2778 行 — 宏观(美元/VIX/利率)+结构化新闻+单次强约束 LLM | **互补**(它信号全,我们更可解释) |
| ③ | **策略生成层** — 趋势/均值回归/事件三族 | `strategy_compiler` — IndicatorStrategy(df出信号)/ ScriptStrategy(`on_bar`/`ctx.buy()`) | **互补**(用它的容器,装我们的生成逻辑) |
| ④ | **评估层** — `judge.py` 268 + `committee.py` 124 | `ai_calibration.py` 341 — 多模型同一 prompt 投票 | **← 我们的差异化** |
| ⑤ | **记忆层** — `store.py` 129 行 JSONL | `analysis_memory.py` **956 行** — Postgres,存决策+市场上下文,检索相似模式,真实收益回填 | **它 carry** |
| ⑥ | **回测层** — `engine.py` 102 行向量化 | `backtest.py` **4373 行** — 服务端,净值/回撤/成交日志/策略快照,多市场 | **它 carry**(40×) |
| ⑦ | **决策层** — 决策报告+仓位建议 | `trading_executor` — 实盘 bot,统一券商账户,paper-only 默认 | **互补**(我们出决策,它做执行) |
| ⑧ | **复盘层** — `reviewer.py` 90 行 | `reflection.py` + `dry_run_deviation.py` **491 行** — AI 决策对真实价格验证、实盘vs回测逐笔滑点 | **它 carry**(它接得到实盘) |

---

## 唯一的真差异:评估层的两种形态

这是本工厂**真正稀缺**的东西。

### QuantDinger:同质并行 · 求共识

源码位置 `fast_analysis.py:1198` — *"LLM call(s) - single or ensemble voting"*

```
模型A ┐
模型B ├→ Counter(decisions) 多数决
模型C ┘
```

N 个模型拿**同一份 prompt、同一个角色**,各自独立出决策再投票。

**作用:降方差** —— 抹平单模型的随机抖动。
**局限:三个模型会一起漏掉同一个系统性盲区。**

### 本工厂:异质对抗 · 求制衡

```
质疑者(挑过拟合/拍脑袋参数) ┐
风险官(最坏亏多少,可否决)  ├→ 制衡裁决
投委会(综合历史经验)        ┘
```

不同角色、**不同 prompt、互相看见对方**。风险官对超红线回撤有一票否决权。

**作用:降系统性盲区** —— 一个视角的漏洞被另一个视角逮住。

> 一个是**投票民主**,一个是**辩论庭审**。QuantDinger 结构上没有后者。

---

## 行动建议

### A. 重活交给 QuantDinger

⑥回测 / ⑤记忆 / ⑧复盘 —— 本工厂的 mock 版继续留作**离线开发与教学**;真跑真实策略时调它的 `/api/indicator/backtest`、`analysis_memory`、`reflection`。40× 体量 + 接实盘的能力,不值得重造。

### B. 评估层做成护城河

让本工厂当 QuantDinger 的 **"AI 评审委员会"** —— 它出候选策略与回测,我们的三角色去挑战、否决、定仓位。

### C. 接法优先 MCP,而非裸 REST

QuantDinger 自带 `mcp_server/`(PyPI `quantdinger-mcp`),标榜 Agent Gateway —— **它本就是为 AI Agent 消费而设计的**。接法见 [quantdinger-setup.md](quantdinger-setup.md)。

---

## 数据能力的现实边界(实测)

| 数据 | QuantDinger 有吗 | **Agent 拿得到吗** |
|---|---|---|
| 行情 | ✅ | ✅ `get_klines` / `get_price` — 已验证出真实数据 |
| 新闻 | ✅ SearchService(Tavily/Google/Bing/SerpAPI 可插拔) | ❌ **Agent Gateway 不暴露** |
| 财报 | ✅ `cn_hk_fundamentals.py` 781 行(PE/PB/ROE/营收/EPS) | ❌ **同样不暴露** |

Agent Gateway 只开放 6 个数据端点:`klines` / `price` / `markets` / `symbols` / `portfolio`×2。

**这就是 [market-insight skill](../.claude/skills/market-insight/SKILL.md) 存在的理由** —— 新闻与财报靠 agent 的搜索与判断力补齐。而且新闻解读本就是判断题,LLM 强于关键词规则。

### 市场支持现状

- **港股** — 默认开启(`SHOW_HK_STOCK=true`),实测可出数据
- **A股** — 代码完备但默认隐藏,设 `SHOW_CN_STOCK=true` 即开
- **加密货币** — 交易所不可达(国内即使挂代理,币安仍返 `451` 地域封锁)
- **实盘交易** — 仅 `Crypto` / `USStock` / `Forex`;港股/A股是 **analysis-only**(`broker_market_policy.py:85`)

---

## 存疑与维护

- 本文基于**源码静态阅读 + 接口实测**。QuantDinger 迭代较快(仓库文档已滞后于 PyPI 实际版本),重大决策前建议复核。
- **文档滞后已知案例**:仓库 `docs/agent/` 描述 mcp v0.2.0/26 工具,PyPI 实际 v0.4.0/33 工具,且安全模型章节过时(v0.4.0 已暴露 `place_quick_order`)。
