# AgentOps Center

> **AI Agent 统一运维、协作与评测平台** —— 把散落在本机的 Claude Code、OpenCode、Codex、自研 HTTP Agent 收进一个工作台：统一发任务、看过程、拉群协作、留痕审计。

<p>
  <img alt="Python" src="https://img.shields.io/badge/Python-3.11%2B-3776AB?logo=python&logoColor=white">
  <img alt="FastAPI" src="https://img.shields.io/badge/FastAPI-0.115%2B-009688?logo=fastapi&logoColor=white">
  <img alt="LangChain" src="https://img.shields.io/badge/LangChain-1.x-1C3C3C">
  <img alt="Vue" src="https://img.shields.io/badge/Vue-3-42B883?logo=vuedotjs&logoColor=white">
  <img alt="Ant Design Vue" src="https://img.shields.io/badge/Ant%20Design%20Vue-4-1677FF?logo=antdesign&logoColor=white">
  <img alt="Status" src="https://img.shields.io/badge/status-phase%203%20wip-orange">
</p>

AgentOps Center 不是又一个 Agent 框架，而是**给已有的 Agent 做一个统一的“运维与协作控制台”**。它不替换 Claude Code / OpenCode 的交互方式，也不要求你把 Agent 重写成 API —— 平台直接驱动本机 CLI 子进程，把流式输出、工具调用、Token 与成本解析成结构化事件；同时内置一套基于 **LangChain** 的平台原生智能体，并用 LangChain 的 `create_agent`（结构化输出）承担多 Agent 的规划与汇总。

---

## 目录

- [为什么需要它](#为什么需要它)
- [功能特性](#功能特性)
- [系统架构](#系统架构)
- [LangChain 编排引擎](#langchain-编排引擎)
- [技术栈](#技术栈)
- [快速开始](#快速开始)
- [使用流程](#使用流程)
- [目录结构](#目录结构)
- [配置](#配置)
- [API 一览](#api-一览)
- [测试](#测试)
- [安全与数据](#安全与数据)
- [路线图](#路线图)
- [文档](#文档)
- [许可证](#许可证)

---

## 为什么需要它

单个 Agent 很好用，一旦同时用上三四个，问题就变成了“**运维问题**”：

- **入口分散**：每个 Agent 一个终端、一套登录态，谁在跑什么、跑完没有，靠人肉记。
- **过程黑盒**：任务跑了 20 分钟，看不到它调了哪些工具、烧了多少 Token、哪一步被权限拒了。
- **协作靠复制粘贴**：想让 Agent A 拆任务、B/C 执行、A 汇总，只能人工搬运上下文。
- **没有账号与审计**：团队里谁能用哪个 Agent、改了什么配置，无据可查。

AgentOps Center 把这些收敛成一个本地 Web 平台：**Agent 有台账、任务有过程、协会有编排、操作有审计**。

## 功能特性

| 模块 | 能力 |
| --- | --- |
| **Agent 管理** | 接入 Claude Code / OpenCode / Codex / 通用 HTTP / **LangChain 内置 Agent**；每个 Agent 可配模型、并发上限、超时、重试；支持启用/禁用、连通性测试、软删除保留历史 |
| **任务中心** | 单次任务可同时派发给多个 Agent；每个任务在独立工作目录 `data/workspaces/task-<id>` 中执行；随时终止 |
| **任务详情** | 逐 Agent 实时输出流、Token 与成本、工具调用 / 模型调用 / 权限拒绝等时间线事件 |
| **执行监控** | 全局实时事件流，按任务、Agent、事件类型、时间筛选 |
| **Agent 群聊** | 建群邀请 Agent、设置角色与主控；`@Agent` 人工分工，或「自动协作」由主控拆分 → 分配 → 汇总；支持步骤交接、审批、暂停 / 恢复 / 终止 / 补充说明 / 重试 / 改派 |
| **系统设置** | 用户与角色（admin / operator / viewer）、项目、审计日志 |
| **平台内置 Agent** | 基于 LangChain 的原生智能体，密钥加密存库，默认接 DeepSeek，无需外挂 CLI 即可跑通全流程 |

## 系统架构

```mermaid
flowchart LR
  U["浏览器<br/>Vue 3 + Ant Design Vue + ECharts"] -->|REST + WebSocket| A["FastAPI 应用"]
  A --> S["服务层<br/>任务调度 · 协作编排 · 事件总线 · Trace · 审计"]
  S --> L["LangChain 编排层<br/>Planner 链 · Summarizer 链 · 回调"]
  S --> DB[("SQLite / PostgreSQL")]
  S --> W[["data/workspaces/task-<id>"]]
  S --> AD["Agent Adapter 层"]
  AD --> C1["Claude Code CLI"]
  AD --> C2["OpenCode CLI"]
  AD --> C3["Codex CLI"]
  AD --> C4["通用 HTTP Agent"]
  AD --> C5["LangChain 内置 Agent<br/>DeepSeek / OpenAI / Anthropic"]
```

几个关键设计：

- **Adapter 隔离差异**：每个 CLI 的流式协议（Claude Code 的 `stream-json`、OpenCode 的事件流等）都被归一化成统一事件模型，上层服务不感知具体 Agent。
- **统一执行入口**：所有适配器都被包成 LangChain `Runnable`（`AdapterRunnable`），任务执行只有一个入口，后续接重试、批处理、hooks 不需要改调用点。
- **子进程 + 独立工作目录**：任务在 `data/workspaces/task-<id>` 里跑，互不污染；平台不修改 Agent 自身配置。
- **事件总线**：执行过程既落库（可回放、可审计），也通过 WebSocket 推给前端（实时可见）。
- **崩溃可恢复**：启动时对未完成的任务与协作会话做恢复，而不是留在“僵尸运行中”。

## LangChain 编排引擎

“自动协作”模式下，主控不再只是又一个被派任务的 Agent —— 规划与汇总由平台自己的 LangChain 链完成（`backend/app/chains/`）：

| 组件 | 作用 |
| --- | --- |
| `llm.py` | `init_chat_model("provider:model", ...)`：DeepSeek / OpenAI / Anthropic 一套写法；未配置密钥时返回 `None`，编排自动回退到“主控 Agent 出计划”的旧路径 |
| `planner.py` | `create_agent(model, tools=[], system_prompt=..., response_format=Plan)`：结构化输出由 LangChain 约束并自动重试校验失败，不再靠正则从自由文本里抠 JSON |
| `plan.py` | 计划校验：成员是否存在、id 是否重复、依赖是否成环；Agent 输出与旧 JSON 解析共用同一份规则 |
| `summarizer.py` | `create_agent(...)` 无工具版，取最后一条 AI 消息作为最终报告 |
| `callbacks.py` | `UsageMetadataCallbackHandler` 统计 token，另加一个 `AsyncCallbackHandler` 把模型/工具调用投影成平台事件 |
| `runnable.py` | `AdapterRunnable`：把任意 AgentAdapter 包装成 `Runnable`，让 CLI / HTTP / 原生 Agent 走同一条执行路径 |

编排产出的步骤依旧落在 `TaskStep` 表里，由 DAG 调度器执行；规划 Agent 本身也会写一条 `AgentRun`，所以 Token、成本、历史在界面上与外部 Agent 一视同仁。规划/汇总失败只会让当前步骤失败，不会拖垮整个协作任务。

## 技术栈

| 层 | 选型 |
| --- | --- |
| 后端 | Python 3.11+ · FastAPI · SQLAlchemy 2.0 (async) · Pydantic Settings · PyJWT · Argon2 · cryptography |
| 编排 / Agent | LangChain 1.4（`create_agent`、`init_chat_model`、`response_format` 结构化输出、`UsageMetadataCallbackHandler`）· 本机 CLI 子进程（Claude Code、OpenCode、Codex）· OpenAI 兼容接口 |
| 存储 | SQLite（默认，零依赖）· PostgreSQL 16（可选，见 `docker-compose.yml`） |
| 前端 | Vue 3 · TypeScript · Vite · Ant Design Vue 4 · ECharts · Vue Router |
| 测试 | pytest（离线用例 + `real_agent` 真实调用用例）· `vue-tsc` 类型检查 |

## 快速开始

**前置条件**：Windows 10/11、Python 3.11+ 与 [uv](https://docs.astral.sh/uv/)、Node.js 18+。要接入的 Agent CLI（如 `claude`、`opencode`）需已安装并登录——平台复用你本机的登录态，不保存它们的凭据。

**方式一（最简单）**：在资源管理器中双击 `启动.bat`，看到 `AgentOps Center -> http://127.0.0.1:8000` 后用浏览器打开；关闭黑色窗口即停止。

**方式二（PowerShell）**：

```powershell
.\start.ps1          # 构建前端，在 http://127.0.0.1:8000 提供完整平台
.\start.ps1 -Dev     # 开发模式：后端 :8000 + Vite 热更新 http://127.0.0.1:5173
.\start.ps1 -Rebuild # 强制重建前端
```

**方式三（手动，便于调试）**：

```powershell
cd backend
uv sync
uv run uvicorn app.main:app --host 127.0.0.1 --port 8000

# 另开一个终端
cd frontend
npm install
npx vite              # 访问 http://127.0.0.1:5173
```

首次打开页面会要求**创建管理员账号**——系统没有默认口令，也没有内置用户。

## 使用流程

1. **Agent 管理 → 接入 Agent**：选择 Claude Code / OpenCode / **LangChain Agent（平台内置）** / Codex / 通用 HTTP，填写模型后保存，点「测试连接」。
   - OpenCode 的模型是 `provider/模型名`（如 `opencode/deepseek-v4.1-flash`）：只填模型名也能用，保存时按本机 `opencode models` 自动补全，运行前还会再兜一次；
   - LangChain Agent 默认走 DeepSeek，密钥读取环境变量 `DEEPSEEK_API_KEY`（也可在页面填写，落库时加密）。
2. **任务中心 → 发送任务**：勾选一个或多个 Agent，输入任务描述。
3. **任务详情**：查看每个 Agent 的实时输出、Token、成本，以及工具调用、模型调用、权限拒绝等事件；可随时终止。
4. **执行监控**：在全局事件流中按任务 / Agent / 事件类型 / 时间筛选，定位异常。
5. **Agent 群聊**：建群邀请 Agent，设置角色与主控；`@Agent` 人工分工，或开启「自动协作」由规划链拆分并分配，右侧面板跟进步骤、交接与审批，必要时暂停 / 恢复 / 改派。
6. **系统设置**：管理用户与角色、项目、审计日志。

## 目录结构

```text
agentops-center/
├─ start.ps1                     # 一键启动（构建前端 + 起后端）
├─ 启动.bat / 启动-开发模式.bat   # Windows 双击入口
├─ docker-compose.yml            # 可选的 PostgreSQL
├─ docs/                         # 需求、架构、数据模型、接入调研、阶段验收
├─ backend/
│  ├─ pyproject.toml
│  ├─ app/
│  │  ├─ main.py                 # 应用装配、静态资源托管、健康检查
│  │  ├─ config.py               # AGENTOPS_* 配置
│  │  ├─ models.py               # 数据模型（用户/项目/Agent/任务/运行/事件/会话…）
│  │  ├─ security.py             # 登录、RBAC、密钥加解密、敏感信息脱敏
│  │  ├─ chains/                 # LangChain 编排层：planner / summarizer / callbacks / runnable
│  │  ├─ adapters/               # claude_code / opencode / codex / http_agent / langchain_agent
│  │  ├─ services/               # task_runner / collab / event_bus / trace / audit
│  │  └─ api/                    # auth / agents / tasks / collab / system
│  └─ tests/                     # pytest 用例与 CLI 流式输出 fixtures
└─ frontend/
   └─ src/
      ├─ pages/                  # Dashboard / Agents / Tasks / TaskDetail / Chat / Monitor / Settings
      ├─ components/             # AppLayout / StatusTag / LevelTag / EventTag
      ├─ api.ts                  # 请求封装与类型
      ├─ auth.ts                 # 登录态
      ├─ live.ts                 # 共享 WebSocket 订阅
      └─ router/                 # 路由与登录守卫
```

## 配置

环境变量前缀 `AGENTOPS_`，也可写入 `backend/.env`：

| 变量 | 默认 | 说明 |
| --- | --- | --- |
| `AGENTOPS_DATA_DIR` | `./data` | 数据库、密钥、任务工作目录 |
| `AGENTOPS_DATABASE_URL` | SQLite（`data/agentops.db`） | PostgreSQL：`postgresql+asyncpg://user:pass@host:port/db` |
| `AGENTOPS_MAX_GLOBAL_CONCURRENCY` | `4` | 同时运行的 Agent 进程上限 |
| `AGENTOPS_JWT_EXPIRE_MINUTES` | `720` | 登录有效期 |
| `AGENTOPS_ORCHESTRATOR_PROVIDER` | `deepseek` | 编排链使用的 provider：`deepseek` / `openai` / `anthropic` |
| `AGENTOPS_ORCHESTRATOR_MODEL` | `deepseek-chat` | 编排链模型名 |
| `AGENTOPS_ORCHESTRATOR_BASE_URL` | 空 | 自定义 OpenAI 兼容地址 |
| `AGENTOPS_ORCHESTRATOR_API_KEY_ENV` | `DEEPSEEK_API_KEY` | 从哪个环境变量读编排密钥 |
| `AGENTOPS_ORCHESTRATOR_API_KEY` | 空 | 直接写入密钥（优先级高于环境变量） |

不配置编排密钥也能跑：自动协作会退回“由主控 Agent 产出计划”的路径，只是多花一次 Agent 调用。

要用 PostgreSQL，先起依赖：

```powershell
docker compose up -d postgres
$env:AGENTOPS_DATABASE_URL = "postgresql+asyncpg://agentops:agentops@127.0.0.1:55432/agentops"
```

## API 一览

所有接口挂在 `/api` 下，交互式文档见 <http://127.0.0.1:8000/docs>。

| 分组 | 代表接口 | 说明 |
| --- | --- | --- |
| 认证 `/api/auth` | `POST /login`、`POST /setup`、`GET /me`、`PATCH /{user_id}` | 首启建管理员、登录、用户管理 |
| Agent `/api/agents` | `GET /adapters`、`GET/POST /agents`、`POST /agents/{id}/test`、`GET /agents/{id}/stats` | 接入、测试、统计 |
| 任务 `/api/tasks` | `POST /tasks`、`GET /tasks/{id}`、`GET /tasks/{id}/events`、`POST /tasks/{id}/cancel` | 派发、详情、事件、终止 |
| 协作 `/api/collab` | `POST /conversations`、`POST /conversations/{id}/messages`、`POST /collab/steps/{id}/retry`、`POST /approvals/{id}/approve` | 群聊、步骤、审批、交接 |
| 系统 `/api/system` | `GET /dashboard/summary`、`GET /projects`、`GET /audit-logs` | 看板、项目、审计 |
| 实时 | `GET /api/events`、`WS /api/ws` | SSE 事件流与实时推送 |

## 测试

```powershell
cd backend
uv run pytest                    # 离线用例，不消耗 Token
uv run pytest -m real_agent -s   # 调用本机真实 Claude Code / OpenCode（消耗少量额度）

cd ..\frontend
npm run typecheck                # vue-tsc 类型检查
npm run build                    # 生产构建（含前端类型检查）
```

## 安全与数据

- **`data/` 永不入库**：SQLite 库、JWT 密钥、密钥加密主密钥、任务工作目录都在 `data/`，已在 `.gitignore` 中排除。
- **Agent 密钥加密存储**：API Key 用主密钥（`data/keys/`）加密后落库，日志与事件中的密钥会被脱敏。
- **无默认口令**：首次启动强制创建管理员；角色分为 admin / operator / viewer。
- **主密钥别丢**：删除 `data/keys/` 后，已加密的 API Key 无法解密。
- **本地优先**：后端默认只监听 `127.0.0.1`，如需对外提供服务，请自行加上 HTTPS 与反向代理。

> 平台通过子进程驱动第三方 Agent CLI，请遵守各 Agent 与模型供应商的服务条款；任务内容与产出由使用者自行负责。

## 路线图

| 阶段 | 内容 | 状态 |
| --- | --- | --- |
| 第一阶段 | 基础平台与 Agent 接入 | ✅ 已完成 |
| 第二阶段 | Agent 群聊与任务协作 | ✅ 已完成 |
| 第三阶段 | 执行监控与 Trace | 🚧 进行中 |
| 第四阶段 | 资产管理 | 📋 规划中 |
| 第五阶段 | 自动化评测 | 📋 规划中 |
| 第六阶段 | 报告中心 | 📋 规划中 |
| 第七阶段 | 平台完善 | 📋 规划中 |

阶段目标与验收标准见 [04 开发计划与验收标准](docs/04-开发计划与验收标准.md)。

## 文档

- [01 · 产品需求分析与技术架构设计](docs/01-需求分析与架构设计.md)
- [02 · 功能模块拆分、数据库设计与核心数据流](docs/02-模块拆分-数据模型-数据流.md)
- [03 · 外部 Agent 真实接入能力调研](docs/03-Agent接入能力调研.md)
- [04 · 开发计划与验收标准](docs/04-开发计划与验收标准.md)
- [05 · 技术栈重构：LangChain 编排 + Vue 3 前端](docs/05-技术栈重构-LangChain与Vue3.md)
- [第一阶段验收报告](docs/phase1-验收报告.md)
- [第二阶段验收报告](docs/phase2-验收报告.md)

## 许可证

当前仓库暂未附带开源许可证，默认保留所有权利。如果准备对外复用，建议补一个 [MIT](https://choosealicense.com/licenses/mit/) 或 Apache-2.0 的 `LICENSE` 文件。
