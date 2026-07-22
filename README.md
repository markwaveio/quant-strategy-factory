# 量化策略工厂 (Quant Strategy Factory)

一条**自动化量化策略流水线**:把「原始市场信息」逐层加工成「可执行的交易决策」,并让 AI 互相挑战、把成败沉淀成记忆。

全程 **mock、离线、可复现** —— 不需要任何 API key 或网络就能端到端跑通一整轮。真实数据源与真实 LLM 评审都留好了接口,填上就能换。

```
① 数据层 ─▶ ② 洞察层 ─▶ ③ 策略生成层 ─▶ ④ 评估层(AI 挑战)
                                              │
   ⑧ 复盘层 ◀─ ⑦ 决策层 ◀─ ⑤ 记忆层 ◀─ ⑥ 回测层 ◀─┘
```

## 快速开始

```bash
# 安装(可编辑模式 + 开发依赖)
pip install -e ".[dev]"

# 跑一整轮流水线,打印决策报告(mock 数据,离线)
qsf run

# 用配置文件跑,并把 Markdown 报告落盘
qsf run --config configs/default.yaml --report reports/latest.md

# 逐层观察数据是怎么加工的(教学向)
python examples/run_pipeline.py

# 跑测试
pytest
```

## 八层设计

| # | 层 | 输入 → 输出 | 关键实现 |
|---|---|---|---|
| ① | **数据层** | 采集 → 标准化 → 特征提取 → `MarketSnapshot` | [data/](src/qsf/data/):mock 采集器(行情/新闻/财报)、清洗、特征(年化波动率、动量、趋势强度、情绪、**黑天鹅强度**) |
| ② | **洞察层** | `MarketSnapshot` → `Insight[]` | [insight/engine.py](src/qsf/insight/engine.py):异常(z-score)、走强/走弱、结构切换(波动率抬升)、事件驱动(黑天鹅/极端情绪) |
| ③ | **策略生成层** | 洞察 → `StrategyProposal[]` | [strategy/](src/qsf/strategy/):趋势(双均线)、均值回归(布林/z-score)、新闻事件三族 |
| ④ | **评估层(AI 挑战)** | 提案+回测 → `EvaluationResult` | [evaluation/](src/qsf/evaluation/):**质疑者**(过拟合/拍脑袋参数)、**风险官**(最坏亏多少,一票否决)、**综合评估官**(投委会裁决) |
| ⑤ | **记忆层** | 结果 → `MemoryRecord` (JSONL) | [memory/store.py](src/qsf/memory/store.py):哪些策略成功过/失败过 + 一句教训,可检索 |
| ⑥ | **回测层** | 提案+行情 → `BacktestMetrics` | [backtest/](src/qsf/backtest/):向量化引擎,计手续费+滑点,产出复利收益/最大回撤/夏普/胜率 |
| ⑦ | **决策层** | 汇总 → `DecisionReport` | [decision/decider.py](src/qsf/decision/decider.py):只把通过评审的策略转为买/卖/持有/回避 + 仓位建议 |
| ⑧ | **复盘层** | 一轮结果 → `ReviewNote` | [review/reviewer.py](src/qsf/review/reviewer.py):通过率、最佳策略、关键收获与改进方向 |

所有层只通过 [core/schema.py](src/qsf/core/schema.py) 定义的**数据契约**通信,层与层解耦——想替换某一层(如 mock 数据换真实源),只需换实现,上层无感。编排见 [core/pipeline.py](src/qsf/core/pipeline.py)。

## 数据采集的三种实现(不是三层)

①数据层是**一个协议、多种实现**,不需要为"agent 采集"新增层级:

```text
①数据层 (DataProvider 协议)
├── MockDataProvider          离线可复现,开发/教学用
├── QuantDingerDataProvider   Python 调 REST,程序化跑
└── market-insight skill      agent 实现,对话式跑  ← .claude/skills/
        ↓ 三者产出同样的 MarketSnapshot + Insight
②洞察层 → ③策略 → ④评估 → ⑥回测 → ⑦决策 → ⑧复盘
```

[**market-insight skill**](.claude/skills/market-insight/SKILL.md) 让 AI agent 直接完成「拉行情+财报 → 算特征 → 出洞察」,输出**严格对齐本工厂的 schema**,可直接喂给下游。

它的算法口径与 [features/extract.py](src/qsf/data/features/extract.py) 一致、阈值与 [insight/engine.py](src/qsf/insight/engine.py) 一致,保证**对话模式与代码模式产出可比**。改阈值时两边要同步。

> 为什么需要它:QuantDinger 的 Agent Gateway **只开放行情**(`get_klines`/`get_price`),不暴露新闻与财报端点。这部分靠 agent 的搜索与判断力补齐——而且新闻解读本就是判断题,LLM 强于关键词规则。

## 评估层:AI 如何"挑战"策略

这是本项目的核心思路——**不让单一模型自说自话,而是让多个角色互相制衡**:

- **质疑者**:交易样本够不够?夏普是不是高得不真实?参数是不是手工凑的?
- **风险官**:最大回撤有没有超红线?黑天鹅暴露大不大?——**回撤超限直接一票否决**。
- **综合评估官**:综合前两者意见 + 记忆层的历史失败次数,做最终裁决。

评估接口 [`LLMJudge`](src/qsf/evaluation/judge.py) 是抽象的:
- 默认 **`MockLLMJudge`**——纯规则、离线、可复现,足以驱动整条链路。
- **`ClaudeLLMJudge`**——真实实现骨架,用最新的 **Claude Opus 4.8** + 自适应思考,输出用 JSON schema 约束。启用方式见下。

## 接入真实能力

### 真实 LLM 评审(Claude)

```bash
pip install -e ".[llm]"
export ANTHROPIC_API_KEY=sk-ant-...
```

把配置里的 `evaluation.provider` 从 `mock` 改成 `anthropic` 即可。三个角色会改由 Claude 扮演;prompt 与 schema 已在 [judge.py](src/qsf/evaluation/judge.py) 里写好。

### 真实数据源:QuantDinger 本地平台(已实现)

把本地量化平台 [QuantDinger](http://127.0.0.1:15000) 作为真实数据源——**8 层逻辑一个字都不用改**,只是把 mock 采集器换成打真实网关的采集器。

```bash
# 1. 确保 QuantDinger 网关在 127.0.0.1:15000 运行
# 2. 导出 agent token(绝不入库,从环境变量读)
export QD_AGENT_TOKEN=qd_agent_xxx      # 建议放 ~/.openviking/quantdinger.env 后 source

# 3. 跑真实数据(标的自动从你的 QuantDinger watchlist 拉取)
qsf run --config configs/quantdinger.yaml --report reports/live.md
```

映射关系:

| QSF 层 | QuantDinger 端点 |
|---|---|
| 行情(OHLCV) | `GET /api/indicator/kline` |
| 新闻(情绪/黑天鹅特征的原料) | `GET /api/global-market/news` |
| 标的来源(watchlist) | `GET /api/market/watchlist/get` |

实现见 [data/quantdinger/](src/qsf/data/quantdinger/):`client.py`(认证+重试)、`collectors.py`(**容错解析**,兼容多种字段名/时间戳/响应包裹形态)、`provider.py`(装配 `MarketSnapshot`)。`configs/quantdinger.yaml` 里 `data.symbols` 留空即从 watchlist 拉;想固定盯某几个就显式写。

> 单标的行情拉取失败会**降级跳过**而非拖垮全局;财报端点当前平台未暴露,返回空不影响其它层。

### 接其它数据源

数据层按 [`DataProvider`](src/qsf/data/provider.py) 协议解耦——照 QuantDinger 的写法(client + 满足 [collectors/base.py](src/qsf/data/collectors/base.py) 协议的采集器 + provider),再在 `build_data_provider()` 里加一个 `mode` 分支即可。`pip install -e ".[data]"` 备有 akshare / yfinance / feedparser。

## 文档

| 文档 | 内容 |
|---|---|
| [docs/quantdinger-setup.md](docs/quantdinger-setup.md) | **接入教程** — 从部署 QuantDinger 到 Codex/Claude Code 接入,含排错表与验收清单(全部命令已实测) |
| [docs/quantdinger-vs-factory.md](docs/quantdinger-vs-factory.md) | **重叠与互补地图** — 本工厂 8 层 vs QuantDinger 逐层对照,哪些该交给它、哪一处是我们的差异化 |
| [.claude/skills/market-insight/](.claude/skills/market-insight/SKILL.md) | **市场洞察** — ①②层:单标的采集与洞察 |
| [.claude/skills/daily-scan/](.claude/skills/daily-scan/SKILL.md) | **每日扫描** — ①②层:无需指定标的,三层池+漏斗+变化检测 |
| [.claude/skills/strategy-review/](.claude/skills/strategy-review/SKILL.md) | **策略评审** — ③⑥④⑦层:生成策略→QuantDinger回测→三角色对抗→决策 |

## 目录结构

```
src/qsf/
  core/         # 数据契约(schema)、配置、流水线编排
  data/         # ① 采集 / 标准化 / 特征
  insight/      # ② 洞察引擎
  strategy/     # ③ 策略族 + 生成器
  evaluation/   # ④ LLMJudge + 评估委员会
  backtest/     # ⑥ 回测引擎 + 绩效指标
  memory/       # ⑤ 经验记忆库
  decision/     # ⑦ 决策器
  review/       # ⑧ 复盘器 + 报告渲染
  cli.py        # 命令行入口
examples/       # 端到端示例
tests/          # 分层测试 + e2e
configs/        # YAML 配置
```

## 免责声明

本项目是**研究与教学脚手架**。mock 数据不代表真实市场;所有产出仅供研究,**不构成任何投资建议**。接入真实数据与实盘前,请自行充分验证并承担风险。

## License

MIT
