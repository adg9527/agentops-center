# AgentOps Center

> **AI Agent 统一运维、协作与评测平台** —— 把散落在本机的 Claude Code、OpenCode、Codex、自研 HTTP Agent 收进一个工作台：统一发任务、看过程、拉群协作、跑评测、管产物。

<p>
  <img alt="Python" src="https://img.shields.io/badge/Python-3.11%2B-3776AB?logo=python&logoColor=white">
  <img alt="FastAPI" src="https://img.shields.io/badge/FastAPI-0.115%2B-009688?logo=fastapi&logoColor=white">
  <img alt="LangChain" src="https://img.shields.io/badge/LangChain-1.4-1C3C3C">
  <img alt="Vue" src="https://img.shields.io/badge/Vue-3-42B883?logo=vuedotjs&logoColor=white">
  <img alt="Ant Design Vue" src="https://img.shields.io/badge/Ant%20Design%20Vue-4-1677FF?logo=antdesign&logoColor=white">
  <img alt="MCP" src="https://img.shields.io/badge/MCP-client%20%2B%20server-6E56CF">
</p>

AgentOps Center 不是又一个 Agent 框架，而是**给已有 Agent 做的统一 “运维 / 协作 / 评测” 控制台**：不替换 Claude Code、OpenCode 的交互方式，直接驱动本机 CLI 或官方 SDK；把流式输出、工具调用、Token 与成本解析成结构化事件，落库、可回放、可评测。

---

## 目录

- [它能做什么](#它能做什么)
- [页面导览](#页面导览)
- [系统架构](#系统架构)
- [接入本地 AI 工具](#接入本地-ai-工具)
- [AgentOps 作为 MCP Server](#agentops-作为-mcp-server)
- [技术栈](#技术栈)
- [快速开始](#快速开始)
- [配置](#配置)
- [API 一览](#api-一览)
- [数据模型](#数据模型)
- [测试](#测试)
- [安全与数据](#安全与数据)
- [路线图](#路线图)
- [文档](#文档)
- [许可证](#许可证)

---

## 它能做什么

| 能力 | 说明 |
| --- | --- |
| **调度与工作流** | 每个任务由 **LangGraph** 状态图编排（plan → 并行 execute → finalize），作业投递到 **RabbitMQ** `agentops.tasks`，由消费者取任务执行并回写进度；没配 broker 时自动退回进程内队列，功能等价 |
| **多 Agent 接入** | Claude Code（CLI 或官方 **Agent SDK**）、OpenCode、Codex、**Ollama 本地模型**、任意 HTTP Agent、平台内置 LangChain Agent；统一的任务、事件、Token、成本模型 |
| **任务执行与观测** | 一任务多 Agent 并行；独立工作目录；实时输出、工具调用时间线、Token/成本、终止；崩溃后自动恢复 |
| **多 Agent 协作** | 群聊里 `@Agent` 人工分工，或「自动协作」由 LangChain 规划链拆解、分配、汇总；步骤 DAG、结构化交接、审批、暂停/恢复/重试/改派 |
| **实时进度** | 群聊里每个 Agent 只发「步骤 + 进度 + 交接」简报，完整输出留在任务详情；任务中心按步骤显示进度条 |
| **本地会话** | 直接读 Claude Code 的 JSONL 与 OpenCode 的 SQLite 会话记录，看每一次提问、回复、工具调用；5 秒同步，按工作目录关联平台任务 |
| **资产登记** | MCP Server（stdio / Streamable HTTP，可真实握手拉 `tools/list`）、Skill（Markdown + 附加文件）、Prompt（`{{变量}}` 模板 + 渲染预览） |
| **自动化评测** | 选 Agent + 技能 + Prompt + MCP 组套件，五维阈值（完成率/工具率/关键词/token/耗时），未达标自动带反馈进入下一轮，直到达标或用完轮数 |
| **产物版本** | 任务结束自动快照工作目录文件，按内容哈希留版本；支持查看任意版本、两版 diff、恢复到指定版本（恢复也记为新版本） |
| **对外提供 MCP** | 平台自身是 MCP Server（7 个工具），WorkBuddy / Claude Code 等客户端可直接接入 |

## 页面导览

| 菜单 | 路由 | 主要能力 |
| --- | --- | --- |
| 工作台 | `/` | 统计卡片、每日执行趋势、各 Agent 次数、**任务关系图 / 思维导图**、最近任务 |
| Agent 群聊 | `/chat` | 会话列表、消息流（Markdown 渲染 + 流式「生成中」气泡）、`@提及`、自动协作、步骤/审批/交接、成员与角色 |
| Agent 管理 | `/agents` | 六种接入方式、按适配器动态渲染配置表单、连接测试、能力矩阵、执行历史与统计 |
| MCP 管理 | `/mcp` | 登记 MCP Server、真实握手测试并保存工具清单、启停 |
| Skill 管理 | `/skills` | Markdown 主文件 + 附加文件 + 标签，版本随修改自增 |
| Prompt 管理 | `/prompts` | `{{变量}}` 自动识别、渲染预览、版本自增 |
| 任务中心 | `/tasks` | 左侧会话列表 + 右侧消息流与任务卡片；按 Agent/状态过滤；进度条；发送任务 |
| 本地会话 | `/sessions` | Claude Code / OpenCode 本机会话，全部/单源筛选，转录查看，关联平台任务 |
| 执行监控 | `/monitor` | 全局事件流，按任务/Agent/类型/时间筛选 |
| 自动化评测 | `/evals` | 评测套件、阈值、多轮 loop、每轮指标与逐用例明细 |
| 文件管理 | `/artifacts` | 评测报告（跨套件轮次）+ 产物版本（任务 → 文件 → 版本，diff、恢复） |
| 系统设置 | `/settings` | 用户与角色（admin/operator/viewer）、项目、审计日志 |

## 系统架构

```mermaid
flowchart LR
  U["浏览器<br/>Vue 3 + Ant Design Vue + ECharts"] -->|REST + WebSocket| A["FastAPI 应用"]
  A --> S["服务层<br/>task_runner · collab · eval · artifacts<br/>local_sessions · graph · event_bus · trace"]
  S --> L["LangChain 1.4<br/>init_chat_model · create_agent · response_format"]
  S --> WF["LangGraph 工作流<br/>plan → execute(Send) → finalize"]
  WF --> Q[["RabbitMQ<br/>agentops.tasks / .progress"]]
  Q --> WK["消费者<br/>TaskRunner.execute_job"]
  S --> DB[("SQLite / PostgreSQL<br/>19 张表")]
  S --> W[["data/workspaces/task-<id>"]]
  S --> AD["Agent Adapter 层"]
  AD --> C1["Claude Code CLI"]
  AD --> C2["Claude Agent SDK"]
  AD --> C3["OpenCode / Codex"]
  AD --> C4["通用 HTTP / LangChain"]
  L --> M["MCP Server<br/>/mcp · stdio"]
  M --> X["WorkBuddy / Claude Code 等外部客户端"]
```

关键设计：

- **Adapter 隔离差异**：每个 CLI 的流式协议归一化成 `AdapterEvent`，上层服务不感知具体 Agent。
- **统一执行入口**：所有适配器包成 LangChain `Runnable`（`AdapterRunnable`），重试/超时/取消在同一处处理。
- **编排用 LangChain**：规划、汇总由 `create_agent(...)` + `response_format=Plan` 完成，结构化输出带校验重试。
- **事件既落库也推送**：`trace_events` 可回放，WebSocket 让界面实时。
- **产物版本化**：任务结束自动快照，内容哈希去重，任何时候都能回到某个版本。

## 接入本地 AI 工具

| 工具 | 接入方式 | 说明 |
| --- | --- | --- |
| **Claude Code** | CLI：`claude -p --output-format stream-json` | 最稳，解析 stdout 得到工具/Token 事件 |
| **Claude Code（Agent SDK）** | `claude-agent-sdk` 官方 Python API | 类型化消息：工具调用、成本、会话 ID 直接拿到，无需解析流 |
| **OpenCode** | CLI：`opencode run --format json` | 模型要 `provider/模型名`，保存时按 `opencode models` 自动补全 |
| **Codex** | CLI 适配器 | 需本机安装 codex |
| **Ollama（本地模型）** | `/api/chat` 流式 NDJSON | 无需密钥、无成本；`base_url` 默认 `http://127.0.0.1:11434`，模型列表从 `/api/tags` 自动发现，输出实时流式显示 |
| **自研 HTTP 服务** | 通用 HTTP 适配器 | POST 一个 JSON，按 dot-path 取结果 |
| **WorkBuddy** | 暂无服务端 API | 本机只开 `127.0.0.1:18488` 的 `/workbuddy/probe` 探针，其 guest SDK 是内嵌网页的 postMessage 桥；它自己能**消费** MCP Server 与 OpenAI 兼容模型 |

## AgentOps 作为 MCP Server

平台自己也暴露 7 个工具，供外部 AI 工具调用：`list_agents`、`get_agent`、`list_tasks`、`get_task`、`create_task`、`list_events`、`dashboard_summary`。

```powershell
# Streamable HTTP：挂在后端 /mcp，用平台 JWT
#   POST http://127.0.0.1:8000/mcp/   Authorization: Bearer <token>

# stdio：由客户端拉起本地进程
cd backend; uv run python -m app.mcp_server
```

```json
{
  "mcpServers": {
    "agentops": {
      "type": "http",
      "url": "http://127.0.0.1:8000/mcp/",
      "headers": { "Authorization": "Bearer <你的 JWT>" }
    }
  }
}
```

## 技术栈

| 层 | 选型 |
| --- | --- |
| 后端 | Python 3.11+ · FastAPI · SQLAlchemy 2.0 (async) · Pydantic Settings · PyJWT · Argon2 · cryptography |
| 编排 / Agent | LangChain 1.4（`init_chat_model`、`create_agent`、`response_format`、usage 回调）· claude-agent-sdk · MCP SDK 2.x |
| 存储 | SQLite（默认，零依赖）· PostgreSQL 16（可选，`docker-compose.yml`） |
| 前端 | Vue 3 · TypeScript · Vite · Ant Design Vue 4 · ECharts · marked + DOMPurify |
| 测试 | pytest（离线用例 + `real_agent` 真实调用）· `vue-tsc` 类型检查 |

## 快速开始

**前置**：Windows 10/11、Python 3.11+ 与 [uv](https://docs.astral.sh/uv/)、Node.js 18+。要接入的 Agent CLI（如 `claude`、`opencode`）需已安装并登录。

**方式一**：双击 `启动.bat`，看到 `AgentOps Center -> http://127.0.0.1:8000` 后用浏览器打开。

**方式二**：

```powershell
# 可选：起 RabbitMQ 作为调度中心（不起则用进程内队列）
docker compose up -d rabbitmq
$env:AGENTOPS_RABBITMQ_URL = "amqp://agentops:agentops@127.0.0.1:5672/"

.\start.ps1          # 构建前端并在 http://127.0.0.1:8000 提供完整平台
.\start.ps1 -Dev     # 开发模式：后端 :8000 + Vite 热更新 http://127.0.0.1:5173
.\start.ps1 -Rebuild # 强制重建前端
```

**方式三（手动）**：

```powershell
cd backend; uv sync; uv run uvicorn app.main:app --host 127.0.0.1 --port 8000
cd frontend; npm install; npx vite
```

首次打开会要求**创建管理员账号**（无默认口令）。

## 配置

环境变量前缀 `AGENTOPS_`，也可写入 `backend/.env`：

| 变量 | 默认 | 说明 |
| --- | --- | --- |
| `AGENTOPS_DATA_DIR` | `./data` | 数据库、密钥、任务工作目录 |
| `AGENTOPS_DATABASE_URL` | SQLite（`data/agentops.db`） | PostgreSQL：`postgresql+asyncpg://user:pass@host:port/db` |
| `AGENTOPS_MAX_GLOBAL_CONCURRENCY` | `4` | 同时运行的 Agent 进程上限 |
| `AGENTOPS_SCHEDULER` | `langgraph` | 任务调度方式：`langgraph`（图 + 队列）或 `legacy`（原来的进程内循环） |
| `AGENTOPS_RABBITMQ_URL` | 空 | AMQP 地址，如 `amqp://agentops:agentops@127.0.0.1:5672/`；留空则用进程内队列 |
| `AGENTOPS_JWT_EXPIRE_MINUTES` | `720` | 登录有效期 |
| `AGENTOPS_ORCHESTRATOR_PROVIDER` | `deepseek` | 编排链 provider：`deepseek` / `openai` / `anthropic` |
| `AGENTOPS_ORCHESTRATOR_MODEL` | `deepseek-chat` | 编排链模型 |
| `AGENTOPS_ORCHESTRATOR_BASE_URL` | 空 | 自定义 OpenAI 兼容地址 |
| `AGENTOPS_ORCHESTRATOR_API_KEY_ENV` | `DEEPSEEK_API_KEY` | 编排密钥所在环境变量 |
| `AGENTOPS_ORCHESTRATOR_API_KEY` | 空 | 直接写密钥（优先） |

不配编排密钥也能跑：自动协作退回「主控 Agent 出计划」，只是多花一次 Agent 调用。

## API 一览

接口都在 `/api` 下，交互式文档见 `http://127.0.0.1:8000/docs`。

| 分组 | 代表接口 | 说明 |
| --- | --- | --- |
| 认证 `/api/auth` | `POST /login`、`POST /setup`、`GET /me` | 首启建管理员、登录、用户管理 |
| Agent `/api/agents` | `GET /adapters`、`GET/POST /agents`、`POST /agents/{id}/test` | 接入、测试、统计 |
| 任务 `/api/tasks` | `POST /tasks`、`GET /tasks`、`GET /tasks/{id}/events`、`POST /tasks/{id}/cancel` | 派发、进度、事件、终止 |
| 协作 `/api/conversations` | `POST /conversations`、`POST /conversations/{id}/messages`、`POST /collab/steps/{id}/retry`、`POST /approvals/{id}/approve` | 群聊、步骤、审批、交接 |
| 图谱 `/api/graph` | `GET /graph/task/{id}`、`GET /graph/task/{id}/mindmap` | 关系图、思维导图 |
| 本地会话 `/api/local-sessions` | `GET /local-sessions`、`GET /local-sessions/claude/{project}/{id}`、`GET /local-sessions/opencode/{id}` | Claude Code / OpenCode 本机记录 |
| 资产 | `/api/mcp-servers`、`/api/skills`、`/api/prompts` | 增删改查 + 连接测试 + 渲染预览 |
| 评测 `/api/evals` | `POST /evals`、`POST /evals/{id}/run`、`GET /evals/{id}/runs`、`GET /evals/recent-runs` | 套件、多轮 loop、报告 |
| 产物 `/api/artifacts` | `GET /artifacts/tasks`、`GET /artifacts/tasks/{id}/versions`、`GET /artifacts/diff`、`POST /artifacts/versions/{id}/restore` | 文件、版本、diff、恢复 |
| 系统 | `GET /api/dashboard/summary`、`GET /api/projects`、`GET /api/audit-logs`、**`GET /api/queue`** | 看板、项目、审计、调度中心状态 |
| 实时 | `GET /events`、`WS /api/ws`、`/mcp` | 事件流、WebSocket、MCP |

## 数据模型

19 张表：`users`、`projects`、`agents`、`agent_runs`、`tasks`、`task_steps`、`handoffs`、`approvals`、`conversations`、`conversation_members`、`messages`、`trace_events`、`audit_logs`、`mcp_servers`、`skills`、`prompt_templates`、`eval_suites`、`eval_runs`、`artifact_versions`。
设计说明见 [02 模块拆分、数据模型与数据流](docs/02-模块拆分-数据模型-数据流.md)。

## 测试

```powershell
cd backend
uv run pytest                    # 离线用例（109 个），不消耗 Token
uv run pytest -m real_agent -s   # 调真实 Claude Code / OpenCode（消耗额度）

cd ..\frontend
npm run typecheck                # vue-tsc
npm run build                    # 生产构建
```

## 安全与数据

- **`data/` 永不入库**：库、JWT 密钥、主密钥、任务工作目录，都在 `.gitignore` 中排除。
- **Agent 密钥加密存储**：API Key 用 `data/keys/` 的主密钥加密后落库，事件里会被脱敏。
- **无默认口令**：首启强制创建管理员；admin / operator / viewer 三级。
- **本地优先**：默认只监听 `127.0.0.1`；对外服务请自行加 HTTPS 与反向代理。
- **产物快照只读原始文件**：恢复是写回工作目录并记为新版本，历史不可变。

> 平台通过子进程/SDK 驱动第三方 Agent，请遵守各 Agent 与模型供应商的服务条款。

## 路线图

| 阶段 | 内容 | 状态 |
| --- | --- | --- |
| 一 | 基础平台与 Agent 接入 | ✅ |
| 二 | Agent 群聊与任务协作 | ✅ |
| 三 | 执行监控与 Trace | 🚧（事件流/树形 Trace 已有，热力图与回放未做） |
| 四 | 资产管理 | 🚧（MCP / Skill / Prompt 管理已交付；统一版本体系未做） |
| 五 | 自动化评测 | 🚧（五维确定性评分 + 多轮 loop 已交付；LLM judge、数据集管理未做） |
| 六 | 报告中心 | 🚧（已与「文件管理」合并：跨套件轮次 + 逐用例明细；导出未做） |
| 七 | 平台完善 | 📋 |

## 文档

- [01 · 需求分析与架构设计](docs/01-需求分析与架构设计.md)
- [02 · 模块拆分、数据模型与数据流](docs/02-模块拆分-数据模型-数据流.md)
- [03 · 外部 Agent 接入能力调研](docs/03-Agent接入能力调研.md)
- [04 · 开发计划与验收标准](docs/04-开发计划与验收标准.md)
- [05 · 技术栈重构：LangChain 编排 + Vue 3 前端](docs/05-技术栈重构-LangChain与Vue3.md)
- [06 · 功能地图与页面导览](docs/06-功能地图与页面导览.md)
- [第一阶段验收报告](docs/phase1-验收报告.md)
- [第二阶段验收报告](docs/phase2-验收报告.md)

## 许可证

当前仓库暂未附带开源许可证，默认保留所有权利；如需复用建议补 MIT 或 Apache-2.0 的 `LICENSE`。
