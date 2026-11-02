# 03 · 外部 Agent 真实接入能力调研

> 原则：优先使用官方非交互接口；所有结论标注"本机实测"或"文档/未验证"。

## 1. Claude Code（本机 2.1.312，✅ 实测）

**接入方式**：CLI 非交互模式（官方 Agent SDK 底层也是同一协议）。

```
claude -p --output-format stream-json --verbose
       --setting-sources project --strict-mcp-config      # 隔离用户级设置/插件/MCP
       --tools Read Glob Grep --allowedTools Read Glob Grep  # 可用工具与免审批工具
       --permission-mode default --no-session-persistence
       [--model haiku] [--max-turns N] [--max-budget-usd X] [--mcp-config file]
  prompt 通过 stdin 传入（避免命令行注入与长度限制）
```

实测事件（stdout 每行一个 JSON）：

| 事件 | 内容 | 平台映射 |
|---|---|---|
| `system/init` | session_id, model, tools, mcp_servers | session.started + 能力检测 |
| `assistant` → `text` | 文本 | agent.message |
| `assistant` → `tool_use` | id, name, input | tool.call（`mcp__<server>__<tool>` 可解析出 MCP） |
| `assistant.message.usage` | input/output/cache tokens | model.call |
| `user` → `tool_result` | content, is_error | tool.result |
| `system/permission_denied` | 被拒工具 | permission.denied |
| `result` | result 文本, total_cost_usd, usage, num_turns, permission_denials | 运行结果 |

**实测结论**：
- 未隔离时一次 "PONG" 调用花费 **$0.108**（加载了用户全局 MCP/插件/CLAUDE.md 约 2.6 万 token）；隔离 + haiku 后一次读文件任务 **$0.0014**。→ 平台默认启用隔离。
- `--tools Read Write Glob --allowedTools Read Glob` 时，Write 被拒绝并产生 `permission_denied` 事件，文件未创建。→ 平台的工具白名单是**真实生效**的权限控制。
- 可获取：token、成本、模型、工具调用、会话 ID；不可获取：内部思维链（不采集）。
- 取消：结束进程树。

## 2. OpenCode（本机 1.19.5，✅ 实测）

**接入方式 A（第一阶段采用）**：CLI `opencode run --format json`。
**接入方式 B（后续）**：`opencode serve` 提供 HTTP API + SSE 事件流（会话常驻，适合群聊多轮）。

```
<npm>/node_modules/opencode-ai/bin/opencode.exe run "<prompt>" --format json [-m provider/model] [--agent x]
环境变量 OPENCODE_CONFIG_CONTENT='{"permission":{"edit":"deny","bash":"deny","webfetch":"deny"}}'
```

- 直接调用 `opencode.exe`（而非 npm 的 `.cmd` 包装），避免 cmd.exe 参数注入风险。
- 实测事件：`step_start`、`tool_use`（part.tool / state.status / input / output，完成时一次性给出）、`text`、`step_finish`（tokens.input/output/reasoning/cache、cost、reason）。
- 实测权限：`edit=deny` 后模型无法写文件，文件未创建。
- stdout 可能混入插件日志（本机有 `[openjev]` 插件输出非 JSON 行）→ 解析器跳过非 JSON 行，可选 `--pure` 禁用外部插件。
- `opencode models` 可列出可用模型 → 用于能力检测。
- 免费模型 `opencode/big-pickle` 报告 cost=0。

## 3. Codex CLI（❌ 本机未安装，适配器标记"未验证"）

- 官方非交互：`codex exec --json --sandbox read-only --skip-git-repo-check -C <dir> -`（prompt 从 stdin）。
- 事件（依据官方文档，未在本机验证）：`thread.started`、`turn.started`、`item.started/item.completed`（item.type = agent_message / command_execution / file_change / mcp_tool_call / web_search / reasoning）、`turn.completed`（usage）、`turn.failed`、`error`。
- 平台实现了 Codex Adapter，但在 UI 中标注 **"未在本环境验证"**；连接测试会如实报告"未安装"。安装 `npm i -g @openai/codex` 并登录后即可验证。

## 4. Gemini CLI（❌ 未安装）

- 官方：`gemini -p "<prompt>" --output-format stream-json`。第一阶段不实现，列入后续 Adapter。

## 5. 通用接入

| 方式 | 第一阶段 | 说明 |
|---|---|---|
| HTTP API | ✅ 通用 HttpAgentAdapter | POST JSON（prompt 字段可配置）→ 从响应 JSON 路径取结果；Header 密钥加密存储。不提供工具事件（标注 unsupported） |
| MCP（作为 Agent） | ⏳ 第四阶段 | 将 MCP Server 的某个工具视为 Agent 入口 |
| Webhook | ⏳ 第二阶段 | 异步回调型 Agent：平台下发任务 + 回调 URL |
| A2A | ⏳ 评估中 | A2A 协议 `message/send` + Agent Card 能力发现 |
| SDK | ⏳ | Claude Agent SDK（Python）可作为 Claude Code Adapter 的替代实现，协议一致 |

## 6. 能力矩阵（Adapter 声明）

| 能力 | Claude Code | OpenCode | Codex | HTTP |
|---|---|---|---|---|
| 连接检测 | ✅ | ✅ | ✅(检测安装) | ✅ |
| 任务发送 | ✅ | ✅ | 未验证 | ✅ |
| 流式事件 | ✅ | ✅ | 未验证 | ❌ |
| 工具调用事件 | ✅ | ✅ | 未验证 | ❌ |
| MCP 调用识别 | ✅（工具名前缀） | 部分（工具名） | 未验证 | ❌ |
| Token 统计 | ✅ | ✅ | 未验证 | 未知 |
| 成本 | ✅ | ✅ | ❌ | 未知 |
| 取消 | ✅（杀进程） | ✅（杀进程） | ✅（杀进程） | ❌ |
| 权限控制 | ✅（工具白名单） | ✅（permission 配置） | ✅（sandbox） | ❌ |
| 会话续接 | ✅（--resume） | ✅（--session） | 未验证 | ❌ |
| 内部思维链 | 不采集 | 不采集 | 不采集 | — |
