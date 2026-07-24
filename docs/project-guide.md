# 项目导览:量化策略工厂怎么读、怎么跑、怎么扩展

这份文档给第一次打开项目的人看。目标是先建立全局地图:每个目录放什么、一次运行会发生什么、产物应该怎么看、接下来要改哪里。

## 一句话定位

量化策略工厂不是实盘交易终端,而是一条研究流水线:

```text
市场数据
→ 市场洞察
→ 策略候选
→ 本地快速回测
→ AI/规则评审
→ 决策报告
→ 研究证据包
→ 富途/QuantDinger 等外部系统复验或模拟交易
```

它的核心价值是先把低质量策略挡在研究阶段,把少数有证据链的候选送去更严肃的外部回测、模拟交易或人工复核。

## 根目录说明

| 路径 | 作用 | 什么时候看 |
|---|---|---|
| `.claude/skills/` | 给 Claude/Codex 使用的 agent 技能,覆盖市场洞察、每日扫描、策略生产、策略评审 | 需要让 agent 半自动完成研究任务时 |
| `.github/workflows/` | GitHub CI 配置 | 需要看自动测试/检查如何跑时 |
| `configs/` | YAML 配置:mock、OpenD、QuantDinger、自选池等 | 每次切换数据源、标的池、风控阈值时 |
| `data_store/` | 本地状态和记忆,例如策略成败 JSONL | 看历史经验沉淀或清理本地实验状态时 |
| `docs/` | 说明文档、研究复盘、接入教程 | 新人入门、复查方法论、接外部系统时 |
| `examples/` | 教学脚本,逐层展示流水线怎么加工数据 | 想理解每层输入输出时 |
| `reports/` | 已生成的策略卡、研究报告、导出策略代码和 JSON 结果 | 看历史实验结果或给外部系统复验时 |
| `scripts/` | 独立研究/运维脚本,如因子 IC、分层回测、富途策略导出 | 做专项研究或外部引擎交叉验证时 |
| `src/qsf/` | 主代码包,所有核心逻辑在这里 | 改系统行为时 |
| `tests/` | 单元测试和端到端测试 | 改代码后确认没有破坏流水线时 |
| `.gitignore` | Git 忽略规则 | 新增产物不确定是否要入库时 |
| `LICENSE` | MIT 许可证 | 发布或复用代码时 |
| `README.md` | 项目首页、快速开始和核心设计 | 第一次了解项目时 |
| `pyproject.toml` | Python 包配置、依赖、命令行入口、ruff/mypy/pytest 配置 | 装依赖、改命令、改检查规则时 |

## `src/qsf` 代码地图

| 模块 | 所属层 | 作用 |
|---|---|---|
| `core/schema.py` | 全局契约 | 定义所有层之间传递的数据结构,如 `MarketSnapshot`、`Insight`、`StrategyProposal`、`BacktestMetrics`、`DecisionReport`、`ResearchReport` |
| `core/config.py` | 配置 | 定义 `Config`、数据源、策略、评估、回测等配置 |
| `core/pipeline.py` | 编排 | 把 8 层串起来,是一次 `qsf run` 的主流程 |
| `data/` | 数据层 | mock、OpenD、QuantDinger 数据源,以及标准化、特征提取、数据质量检查 |
| `insight/` | 洞察层 | 从特征中识别异常、走强/走弱、波动结构切换、事件冲击 |
| `strategy/` | 策略生成层 | 根据洞察生成趋势、均值回归、新闻事件等策略候选,并支持参数网格搜索 |
| `backtest/` | 回测层 | 本地 smoke backtest、绩效指标、横截面因子验证 |
| `evaluation/` | 评估层 | 质疑者、风险官、投委会三角色评审,默认是可复现规则评审,也预留真实 LLM |
| `memory/` | 记忆层 | 把每轮策略成败和教训写入本地 JSONL |
| `decision/` | 决策层 | 把通过评审的策略转成买入、卖出、持有、回避和建议仓位 |
| `review/` | 复盘层 | 汇总本轮通过率、最佳策略、风险点,并渲染决策 Markdown |
| `research/` | 研究证据包 | 把候选策略、回测、评审、下一步整理成可归档 Markdown 和 JSON |
| `cli.py` | 命令入口 | 提供 `qsf run`、报告导出、研究证据包导出、策略 JSON 导出 |

## 一次运行发生什么

运行:

```bash
qsf run --config configs/default.yaml --report reports/latest.md
```

主流程在 `src/qsf/core/pipeline.py`:

1. 数据层构建 `MarketSnapshot`
2. 洞察层生成 `Insight[]`
3. 策略层生成 `StrategyProposal[]`
4. 回测层为每个策略生成 `BacktestMetrics`
5. 评估层输出 `EvaluationResult`
6. 记忆层把成败写入 `data_store/memory/memory.jsonl`
7. 决策层输出 `DecisionReport`
8. 复盘层输出 `ReviewNote`
9. 研究层输出 `ResearchReport`

最终可以得到三类产物:

| 产物 | 命令参数 | 用途 |
|---|---|---|
| 决策报告 Markdown | `--report reports/latest.md` | 给人看的本轮决策摘要 |
| 研究证据包 Markdown | `--research-report reports/research.md` | 记录每个候选策略为什么通过、观察或否决 |
| 策略候选 JSON | `--strategies reports/strategies.json` | 供模拟交易、外部回测或自动化系统读取 |

注意:策略卡和研究证据包是 Markdown 文档,不是富途策略编辑器代码。要贴进富途,先用策略候选 JSON 生成 `.py` 文件。

## 推荐阅读顺序

第一次看项目,建议按这个顺序:

1. `README.md`:先了解项目目标和 8 层设计
2. `docs/project-guide.md`:看目录和运行流程
3. `examples/run_pipeline.py`:看每层产物如何变化
4. `src/qsf/core/schema.py`:看所有数据契约
5. `src/qsf/core/pipeline.py`:看主流程如何编排
6. `src/qsf/evaluation/judge.py`:看系统如何质疑策略
7. `src/qsf/research/reporter.py`:看策略候选如何归档成证据包
8. `docs/factor-research-02.md`:看一次真实研究如何发现并修正方法论陷阱

## 日常使用路径

### 0. 学员一键部署

让学员 clone 仓库后对自己的 agent 说:

```text
请阅读 skills/qsf-student-installer/SKILL.md,帮我安装部署这套量化策略工厂。
```

或者手动执行:

```bash
bash scripts/install.sh
```

脚本会创建 `.venv`、安装依赖、安装 agent skills、运行测试,并生成离线 smoke 报告。

### 1. 离线跑通

```bash
pip install -e ".[dev]"
qsf run
pytest
```

这条路径不用 API key,用于确认环境和主流程没坏。

### 2. 用富途 OpenD 做真实数据研究

```bash
pip install -e ".[opend]"
qsf run --config configs/opend-hk.yaml \
  --report reports/run-opend-hk.md \
  --research-report reports/research-opend-hk.md \
  --strategies reports/strategies-opend-hk.json
```

OpenD 是当前默认真实数据源,提供历史行情和部分基本面字段。QSF 仍然负责本地 smoke backtest、评审和证据包。QuantDinger 暂时关闭,需要服务端回测/执行联动时再单独启用。

如果目标是每天都有研究候选,使用每日研究模式:

```bash
qsf run --config configs/opend-hk-daily-research.yaml \
  --report reports/run-opend-hk-daily.md \
  --research-report reports/research-opend-hk-daily.md \
  --strategies reports/strategies-opend-hk-daily.json
```

如果目标是只在机会过线时才生成候选,使用严格机会模式:

```bash
qsf run --config configs/opend-hk-strict-opportunity.yaml \
  --report reports/run-opend-hk-strict.md \
  --research-report reports/research-opend-hk-strict.md \
  --strategies reports/strategies-opend-hk-strict.json
```

### 3. 把候选送去富途复验

```bash
python scripts/export_futu_strategy.py \
  --from-strategies reports/strategies-opend-hk-daily.json \
  --output-dir reports/futu \
  --backtest-range "最近 500 个交易日/约 2 年" \
  --initial-cash "100000 HKD" \
  --bar-note "日 K / RTH / CLOSE" \
  --approved-only
```

这一步是把候选策略 JSON 里的趋势策略转成富途策略编辑器可粘贴的代码。每个 `.py` 文件仍只用于富途回测或模拟验证,不是实盘授权。
导出的文件顶部会写入标的池、建议回测区间、初始资金、K 线规则、快慢周期和开仓数量,方便在富途里按同一口径复验。

如果只想手动导出一组双均线参数:

```bash
python scripts/export_futu_strategy.py --fast 8 --slow 40 -o reports/futu-strategy.py
```

### 4. 做专项因子研究

常用脚本:

| 脚本 | 作用 |
|---|---|
| `scripts/build_universe.py` | 构建研究标的池 |
| `scripts/factor_ic.py` | 做因子 IC 检验 |
| `scripts/factor_backtest.py` | 做分层回测和成本敏感性检查 |
| `scripts/regime_conditional.py` | 做状态条件化检验 |

专项研究结论应进入 `docs/` 或 `reports/`,不要只留在终端输出里。

## 每日扫描应该怎么接

每日扫描不要一上来对所有标的跑全部策略。推荐漏斗:

```text
全市场/自选池
→ 异常扫描:动量、波动、成交量、跳空、新闻、估值、换手
→ Top N 候选
→ 市况/事件路由
→ 少量匹配策略
→ 参数搜索和本地回测
→ AI/规则评审
→ 研究证据包
→ 外部回测或模拟交易
```

当前项目已经有 `.claude/skills/daily-scan/` 作为 agent 侧每日扫描入口。代码侧的长期方向是把这个漏斗固化为 `qsf.scan` 和策略模板 metadata,让每日运行自动先筛候选,再生成策略。

## 如何判断一个结果能不能继续推进

不要只看收益。至少同时看:

| 检查 | 为什么重要 |
|---|---|
| 是否跑赢买入持有 | 分清 alpha 和 beta |
| 样本外是否仍有效 | 防止只在训练期好看 |
| 交易次数是否足够 | 防止少数偶然交易撑起结论 |
| 最大回撤是否过红线 | 控制真实持有体验和风险 |
| 数据质量是否干净 | 脏数据会制造假 alpha |
| 参数是否来自搜索 | 避免拍脑袋参数 |
| 多标的是否稳健 | 避免单标的过拟合 |

只有本地研究过关的候选,才值得送去富途、QuantDinger 或模拟账户继续验证。

## 扩展入口

| 想做什么 | 优先改哪里 |
|---|---|
| 接新数据源 | `src/qsf/data/provider.py` 和 `src/qsf/data/<source>/` |
| 加新特征 | `src/qsf/data/features/extract.py` 和 `FeatureSet` |
| 加新洞察规则 | `src/qsf/insight/engine.py` |
| 加新策略族 | `src/qsf/strategy/families.py`、`generator.py`、`search.py` |
| 改评审标准 | `src/qsf/evaluation/judge.py` 和 `committee.py` |
| 改仓位/动作逻辑 | `src/qsf/decision/decider.py` |
| 改报告格式 | `src/qsf/review/report.py` 或 `src/qsf/research/reporter.py` |
| 接模拟交易/外部回测 | 优先读取 `--strategies` 导出的 JSON |

## 关键边界

- QSF 的本地回测是 smoke backtest,用于快速筛选,不是实盘级撮合。
- 富途 OpenD 主要负责数据和交易通道,不是公开的策略回测任务 API。
- 富途策略编辑器回测适合作为外部复验,当前更偏半自动。
- 任何进入实盘前的策略,都应先经过外部回测、模拟交易、小资金验证和人工风控确认。
