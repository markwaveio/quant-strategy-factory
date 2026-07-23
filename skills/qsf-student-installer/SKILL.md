---
name: qsf-student-installer
description: 引导学生在自己的 agent 环境中安装、部署、验证和使用 Quant Strategy Factory。当用户说"安装量化策略工厂""部署 QSF""帮我跑通 quant-strategy-factory""学生环境安装"或刚 clone 了本仓库时使用。会检测平台与 Python 环境,创建配置,运行安装脚本,安装相关 skills,跑离线 smoke test,可选接入富途 OpenD,并说明如何阅读报告和继续研究。
---

# QSF 学员部署引导

你正在帮助学生在自己的机器和 agent 上部署 Quant Strategy Factory。按步骤推进,一次只问一个必要问题。不要跳过验证,不要给实盘交易建议。

## 目标

装完后学生应能做到:

- 在本地运行 `qsf run`
- 生成决策报告、研究证据包、策略候选 JSON
- 让 agent 使用仓库内的市场洞察、每日扫描、策略生产、策略评审 skills
- 默认接入富途 OpenD 做真实历史数据研究;mock 只用于离线自检

## 第 1 步:确认仓库位置

让学生进入仓库根目录,或你自己定位:

```bash
pwd
ls README.md pyproject.toml src/qsf
```

如果这些文件不存在,请学生先 clone:

```bash
git clone https://github.com/markwaveio/quant-strategy-factory.git
cd quant-strategy-factory
```

## 第 2 步:说明安装形式

告诉学生本项目是本地 Python 研究工具,不是云服务:

```text
GitHub 仓库
→ 本地 .venv Python 环境
→ qsf 命令行
→ agent skills
→ reports/ 或工作区产出
```

安装阶段先跑 mock 离线自检,保证没有行情环境也能确认代码可用。真实数据研究默认走富途 OpenD。QuantDinger 先不启用,除非老师或学生后续明确要求。

## 第 3 步:询问 agent 平台

问学生使用哪个 agent 平台:Claude Code、Codex、OpenCLAW、其它。

默认 skill 安装目录:

| 平台 | skills 目录 |
|---|---|
| Claude Code | `~/.claude/skills` |
| Codex | `~/.codex/skills` |
| OpenCLAW | `~/.openclaw/skills` |

如果学生不确定,先用安装脚本自动探测。

## 第 4 步:生成本地配置

如果需要自定义路径,复制配置模板:

```bash
cp config/qsf.config.example config/qsf.config
```

只在学生明确需要时修改:

- `SKILLS_DIR`:skill 装到哪里,默认 `auto`
- `WORKSPACE_DIR`:报告和 smoke test 产物放哪里
- `INSTALL_OPEND`:是否安装富途 OpenD 依赖,默认 `true`
- `DEFAULT_CONFIG`:默认真实数据配置,默认 `configs/opend-hk.yaml`
- `SMOKE_CONFIG`:离线自检配置,默认 `configs/default.yaml`
- `RUN_TESTS`:安装后是否跑测试
- `RUN_SMOKE`:安装后是否跑离线验证

如果学生是第一次安装,建议保持默认,不要一上来改很多项。

## 第 5 步:运行一键安装

执行:

```bash
bash scripts/install.sh
```

脚本会:

- 创建 `.venv`
- 安装 `quant-strategy-factory[dev,opend]`
- 安装本仓库的 agent skills
- 创建工作区
- 运行 `ruff`、`pytest`
- 跑一次 mock 离线 smoke run,生成报告

如果已有旧版 skill,默认跳过不覆盖。升级时才执行:

```bash
git pull
bash scripts/install.sh --update
```

## 第 6 步:验证结果

确认这些检查通过:

```bash
source .venv/bin/activate
PYTHONPATH=src python -m qsf.cli run
pytest
```

若 `qsf run` 命令本身因 editable path 问题不可用,先用:

```bash
PYTHONPATH=src python -m qsf.cli run
```

这不影响源码可用性。等环境修好后再追求裸 `qsf` 命令。

## 第 7 步:教学生看产物

安装脚本默认把 smoke run 产物写到:

```text
~/quant-strategy-factory-workspace/reports/
```

重点看:

| 文件 | 用途 |
|---|---|
| `decision-smoke.md` | 本轮决策摘要 |
| `research-smoke.md` | 每个候选策略的证据、风险和下一步 |
| `strategies-smoke.json` | 给外部回测或模拟交易系统读取的候选策略 JSON |

强调:Markdown 策略卡/研究证据包是研究产物,不是富途策略编辑器代码,也不是实盘指令。

## 第 8 步:运行默认 OpenD 真实数据研究

只有学生已经安装并启动 Futu OpenD 时才做。若没有 OpenD,保留 mock 自检结果即可,不要改用 QuantDinger。

1. 确认 OpenD 在 `127.0.0.1:11111`
2. 运行:

```bash
PYTHONPATH=src python -m qsf.cli run \
  --config configs/opend-hk.yaml \
  --report reports/run-opend-hk.md \
  --research-report reports/research-opend-hk.md \
  --strategies reports/strategies-opend-hk.json
```

如需生成富途策略编辑器可粘贴代码,继续运行:

```bash
python scripts/export_futu_strategy.py \
  --from-strategies reports/strategies-opend-hk.json \
  --output-dir reports/futu \
  --approved-only
```

提醒学生 OpenD 有历史 K 线额度限制,不要随便扩大标的池。

QuantDinger 暂时关闭。除非明确要研究 QuantDinger 服务端回测/执行联动,否则不要读取 `configs/quantdinger.yaml`,也不要要求 `QD_AGENT_TOKEN`。

## 第 9 步:开始使用

给学生这几个启动话术:

```text
请阅读 README.md 和 docs/project-guide.md,解释这套量化策略工厂的流程。
```

```text
跑一轮 qsf mock 离线自检流程,解释决策报告和研究证据包。
```

```text
用默认 OpenD 配置 configs/opend-hk.yaml 跑一轮真实数据研究,但不要给实盘建议。
```

```text
根据 research report,挑出可以进入富途回测或模拟交易观察的候选。
```

## 边界

- 不自动实盘交易。
- 不把本地 smoke backtest 当成富途官方回测。
- 不伪造行情、财报、新闻或回测结果。
- 不覆盖学生已有 skill,除非使用 `--update`。
- 不把报告公开发布,除非学生明确要求。
