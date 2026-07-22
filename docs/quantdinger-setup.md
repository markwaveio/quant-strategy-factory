# QuantDinger × AI Agent 接入教程

> 从零部署到 Codex / Claude Code 调用真实行情。全部步骤已实测跑通。
> QuantDinger v3.0.31 · quantdinger-mcp **v0.4.0 / 33 工具** · 实测 2026-07-22

---

## 1. 部署 QuantDinger

一条命令，不需要 git clone，不需要 Node.js。

```bash
# 默认装到 ~/quantdinger;重跑同一条命令 = 拉最新镜像并重启
curl -fsSL https://raw.githubusercontent.com/brokermr810/QuantDinger/main/install.sh | bash
```

装完打开 Web UI，用 `quantdinger` / `123456` 登录，**立刻改掉默认密码**。

### 两个端口要分清

| 用途 | 默认 | 本机实例(定制) |
|---|---|---|
| 前端 UI | 8888 | **18080** — 浏览器打开这个 |
| 后端 API | 8888 | **15000** — Agent/MCP 连这个 |

> ⚠️ 官方文档通篇写 `localhost:8888`，那是默认值。端口在 `.env` 的 `FRONTEND_PORT` / `BACKEND_PORT` 里。
> **照抄文档端口是最常见的失败原因。**

```bash
# 确认自己的端口
grep -E "^FRONTEND_PORT|^BACKEND_PORT" ~/quantdinger/.env

# 健康检查(期望 200)
curl -s -o /dev/null -w "%{http_code}\n" http://127.0.0.1:15000/api/agent/v1/health
```

---

## 2. 签发 Agent Token

> 设计上 **Agent 永远不能给自己签发 token**，必须由登录的人签。

### 路径 A · 界面（推荐）

登录 Web UI → **Profile → My Agent Token → Issue Token** → 起名（如 `codex`）→ 勾选权限 → **当场复制，只显示一次**。

### 路径 B · API

```bash
curl -X POST http://127.0.0.1:15000/api/agent/v1/me/tokens \
  -H "Authorization: Bearer <你的用户JWT>" \
  -H "Content-Type: application/json" \
  -d '{"name":"codex","scopes":"R,B","markets":"*","expires_in_days":30}'
```

### 权限档位

| Scope | 能力 | 建议 |
|---|---|---|
| `R` | 读：行情、策略、任务 | 必选 |
| `B` | 回测 / 模拟 | 必选 |
| `W` | 写工作区：存指标、建策略 | 想让 AI 写策略就加 |
| `N` | 通知等副作用 | 按需 |
| `T` | **交易** | 见下方警告 |
| `C` | 凭证 | 仅管理员可签 |

> 🚨 **`paper_only: true` 是你唯一的资金安全阀。**
> 带 `T` 的 token 默认锁定模拟盘；要签出能碰真钱的 token，必须显式传 `ack_live_trading_risk=true`。
> **没有充分理由，永远不要关掉 paper_only** —— v0.4.0 的 MCP 已经暴露了 `place_quick_order`，关掉它 AI 就能下真实订单。

---

## 3. 配置 AI 客户端

MCP 服务器发布在 PyPI，**无需安装** —— `uvx` 首次运行自动下载缓存。先确保有 uvx：

```bash
brew install uv
which uvx          # 记下绝对路径,下面要用
```

### Codex — `~/.codex/config.toml`

```toml
[mcp_servers.quantdinger]
command = "/opt/homebrew/bin/uvx"
args = ["quantdinger-mcp"]
startup_timeout_sec = 120

[mcp_servers.quantdinger.env]
QUANTDINGER_BASE_URL = "http://127.0.0.1:15000"
QUANTDINGER_AGENT_TOKEN = "qd_agent_你的token"
```

### Claude Code — `~/.claude.json` 或项目 `.mcp.json`

```json
{
  "mcpServers": {
    "quantdinger": {
      "command": "uvx",
      "args": ["quantdinger-mcp"],
      "env": {
        "QUANTDINGER_BASE_URL": "http://127.0.0.1:15000",
        "QUANTDINGER_AGENT_TOKEN": "qd_agent_你的token"
      }
    }
  }
}
```

> ⚠️ **`command` 用绝对路径**。客户端启动子进程时 PATH 未必包含 Homebrew 目录，写 `uvx` 可能报 command not found。

改完校验语法，然后**重启客户端**（配置只在启动时读）：

```bash
# TOML
python3.12 -c "import tomllib;tomllib.load(open('$HOME/.codex/config.toml','rb'));print('OK')"

# JSON
python3 -m json.tool ~/.claude.json > /dev/null && echo OK
```

---

## 4. 跨设备部署（可选）

配置文件写在**运行 AI 客户端的那台机器**上。QuantDinger 默认只绑 `127.0.0.1`，别的设备连不上。

| 方案 | 做法 | 适合 |
|---|---|---|
| A 同机 | `127.0.0.1:15000`，零改动 | 单机使用 |
| B 直连 | `.env` 里 `BACKEND_PORT=<内网IP>:15000` 后重启；客户端指向该 IP | 少量设备 |
| C 常驻 MCP | 见下 | **多设备，推荐** |

**方案 C** —— 在服务器上把 MCP 跑成 HTTP 服务，**token 只存这一处**，其它设备配置里没有密钥：

```bash
QUANTDINGER_BASE_URL=http://127.0.0.1:15000 \
QUANTDINGER_AGENT_TOKEN=qd_agent_xxx \
QUANTDINGER_MCP_TRANSPORT=streamable-http \
QUANTDINGER_MCP_HOST=0.0.0.0 \
QUANTDINGER_MCP_PORT=7800 \
uvx quantdinger-mcp
```

其它设备只要一行：`url = "http://<服务器IP>:7800"`

---

## 5. 测试验证

不用等重启客户端，命令行就能逐层验证。

```bash
TOK="qd_agent_你的token"
H="Authorization: Bearer $TOK"

# ① 网关活着(不需要 token)
curl -s http://127.0.0.1:15000/api/agent/v1/health

# ② token 有效 + 看权限  ← 最关键的一步
curl -s -H "$H" http://127.0.0.1:15000/api/agent/v1/whoami | python3 -m json.tool

# ③ 真实行情(港股腾讯)
curl -s -H "$H" "http://127.0.0.1:15000/api/agent/v1/klines?market=HKStock&symbol=00700&timeframe=1d&limit=3"
```

**② 的正确输出**（重点看 `scopes` 和 `paper_only`）：

```json
{ "data": {
    "agent_name": "codex",
    "scopes": ["B","N","R","T","W"],
    "markets": ["*"],
    "paper_only": true,
    "rate_limit_per_min": 60 } }
```

### ④ MCP 协议层（模拟客户端调用）

```bash
printf '%s\n%s\n%s\n' \
 '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"t","version":"1"}}}' \
 '{"jsonrpc":"2.0","method":"notifications/initialized"}' \
 '{"jsonrpc":"2.0","id":2,"method":"tools/list"}' \
| QUANTDINGER_BASE_URL=http://127.0.0.1:15000 QUANTDINGER_AGENT_TOKEN="$TOK" \
  uvx quantdinger-mcp 2>/dev/null | grep -o '"name":"[a-z_]*"' | head -40
```

✅ 能列出 33 个工具名 = 全链路打通，重启客户端即可使用。

### 在 AI 客户端里的第一句话

> 用 quantdinger 的 whoami 工具看看我的权限

一次确认三件事：token 有效、权限对不对、连的是不是目标实例。

---

## 常见问题

| 症状 | 原因与解法 |
|---|---|
| 401 | token 错/过期，或还是占位符文本。重新签发 |
| 404 | 端点路径错。K线是 `/api/agent/v1/klines`，不在 `/markets/` 下 |
| 连接被拒 | 端口错（多半照抄了文档的 8888），或 QuantDinger 没起来 |
| command not found | `command` 没写 uvx 绝对路径 |
| 工具没出现 | 客户端没重启；或 TOML/JSON 语法坏了（用上面的校验命令） |
| 加密货币无数据 | 交易所 API 网络不可达。国内即使挂代理，币安也可能返回 `451` 地域封锁。港股/美股不受影响 |
| A股看不到 | `SHOW_CN_STOCK` 默认 `false`，在 env 里改成 `true`。港股默认已开启 |

---

## 验收清单

- [ ] Web UI 能打开且已改默认密码（`http://localhost:18080`）
- [ ] 网关健康检查返回 200（`/api/agent/v1/health`）
- [ ] whoami 返回预期的 scopes，且 `paper_only` 为 `true`
- [ ] 能拉到真实 K 线（HKStock 00700 / USStock AAPL）
- [ ] MCP 握手成功并列出 33 个工具
- [ ] 客户端重启后，AI 能调用 whoami

---

## 附注：文档滞后

仓库内 `docs/agent/` 描述的是 mcp **v0.2.0 / 26 工具**，PyPI 实际已是 **v0.4.0 / 33 工具**：

- 新增：`compile_strategy_code`、`save_strategy_source`、策略源码版本管理、`runtime_overview`、`stop_strategy` 等
- **安全模型章节已过时**：README 称「MCP 不暴露实盘交易」，但 v0.4.0 已有 `place_quick_order`

**以实际 `tools/list` 输出为准。**

官方文档位置：`docs/agent/MCP_SETUP.md`、`docs/agent/AGENT_QUICKSTART.md`
