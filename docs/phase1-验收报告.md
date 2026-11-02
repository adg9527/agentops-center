# 第一阶段验收报告：基础平台与 Agent 接入

> 日期：2026-10-30 · 环境：Windows 11 / Python 3.12（uv venv）/ Node 24 / Claude Code 2.1.312–2.1.314 / OpenCode 1.19.5

## 1. 已完成功能

| 模块 | 内容 | 状态 |
|---|---|---|
| 工程骨架 | FastAPI 模块化单体（api / adapters / services）+ React/TS/Vite/AntD 暗色控制台 | ✅ 真实实现 |
| 认证 | 首次启动网页创建管理员（无默认口令）、Argon2 口令、JWT 登录 | ✅ |
| 权限 | admin / operator / viewer 三级 RBAC，后端强制校验 | ✅ |
| Dashboard | Agent 总数/在线、任务各状态、执行成功率、平均耗时、Token、成本、每日执行图、各 Agent 执行次数、最近任务；时间/项目/Agent 筛选；未实现模块显示"第 N 阶段"而非虚构数字 | ✅ |
| Agent 接入 | 增删改查（软删除保留历史）、启停、连接测试、能力矩阵、执行历史、调用统计、配置版本号、动态配置表单、高风险配置提示 | ✅ |
| Adapter | Claude Code（CLI stream-json）✅实测、OpenCode（CLI json）✅实测、通用 HTTP ✅测试、Codex（CLI exec --json）⚠️**未验证**（未安装） | 见下 |
| 任务发送 | 单/多 Agent 并行执行、独立工作目录、状态流转、取消（杀进程树）、超时、重试（每次尝试独立记录）、全局与单 Agent 并发上限、重启恢复 | ✅ |
| Trace | 统一事件表（trace_id/span_id/parent_span_id…），任务=Trace、AgentRun=Span、工具调用=子 Span；实时 WebSocket 推送；任务时间线；全局事件流与多维筛选 | ✅（第三阶段扩展瀑布图/回放） |
| 安全 | 密钥 Fernet 加密且 API 永不回传、事件摘要与输出脱敏、审计日志、默认最小权限工具集 | ✅ |

## 2. 真实运行测试结果

### 2.1 自动化测试（`uv run pytest`）— **18 passed**

覆盖：口令哈希、密钥加解密、脱敏规则；基于**真实录制输出**的 Claude Code / OpenCode 解析器；Codex 文档事件解析；Claude 最小权限参数；认证流程与 401/403；Agent CRUD 且密钥不出现在任何 API/审计响应中；多 Agent 任务 + Trace 串联（同 trace_id、工具 Span 父子关系）+ 输出脱敏；失败重试；取消杀进程；超时；通用 HTTP Adapter 对真实本地 HTTP 服务；以及 3 个环境问题回归测试。
（CLI 运行器的取消/超时用 `tests/fake_cli.py` 子进程驱动，该替身仅存在于测试中，不对产品暴露。）

### 2.2 真实 Agent 端到端（`uv run pytest -m real_agent -s`）— **2 passed**

任务："读取 note.txt 并原样返回"，文件内容 `AGENTOPS-MAGIC-42`，经平台 API 完整链路（建 Agent → 测试连接 → 发任务 → 采集事件 → 取结果）：

| Agent | 结果 | 工具调用 | Token 入/出 | 成本 |
|---|---|---|---|---|
| Claude Code（haiku，隔离模式） | ✅ `AGENTOPS-MAGIC-42` | Read（工作目录内） | 12,848 / 131 | $0.00082 |
| OpenCode（opencode/big-pickle） | ✅ `AGENTOPS-MAGIC-42` | read（工作目录内） | 27,029 / 93 | $0（免费模型） |

另外在演示实例中对两个 Agent 同时下发"列出工作目录"任务：均完成，19 个事件落库，WebSocket 实时收到 `task.started → agent.run.started → session.started → agent.message → model.call → agent.run.completed`。

### 2.3 权限控制实测

- Claude Code：可用工具 `Read Write Glob`、免审批 `Read Glob` → Write 被拒，产生 `permission.denied` 事件，文件未创建。
- OpenCode：`edit/bash/webfetch = deny` → 无法写文件，文件未创建。

### 2.4 前端

`tsc -b` 无错误；`vite build` 成功；开发服务器与单端口生产模式（:8000）均验证可访问、路由/代理/WebSocket 正常。

## 3. 验证中发现并修复的问题

1. **OpenCode 越出工作目录（安全问题）**：OpenCode 依据继承的 `$PWD` 而非进程 cwd 解析项目目录，曾读取到平台后端目录。修复：显式 `--dir <workspace>`，并为所有 CLI 子进程设置 `PWD`。已加回归测试。
2. **本机系统代理拦截回环请求**：环境变量 `http_proxy=127.0.0.1:10808` 使本地 HTTP Agent 请求被代理返回 503。修复：HTTP Adapter 对 localhost/127.x/::1 不走系统代理。
3. **本机透明加密（DLP）**：本机文件加密软件对 python.exe 读取的 `.html/.ts/.tsx` 返回密文（`%TSD-Header-###%`），导致后端无法直接返回 `dist/index.html`。修复：后端根据 Vite `manifest.json`（可正常读取）生成页面外壳。
4. **未隔离的 Claude Code 成本偏高**：加载用户全局 MCP/插件时一次极简调用 $0.108；默认启用隔离后降至约 $0.001。

## 4. 尚未完成 / 已知问题

| 项 | 说明 |
|---|---|
| Codex Adapter | 本机未安装 Codex CLI，Adapter 依据官方文档实现并在 UI 标注"未验证"。安装 `npm i -g @openai/codex` 并登录后可执行验证 |
| Gemini CLI | 未安装，未实现 |
| PostgreSQL | 代码兼容（asyncpg、无方言特有类型），`docker-compose.yml` 已提供，但 Docker Desktop 未运行，**未实测**；当前默认 SQLite |
| 数据库迁移 | 使用 `create_all`（仅建新表）。第二阶段新增表前引入 Alembic，修改既有库前先备份 |
| 任务队列 | 进程内 asyncio 调度（Windows 不支持 Celery prefork），单进程部署；多进程需换 Redis 队列 |
| 浏览器截图验收 | 本会话 Chrome 扩展未连接，UI 通过 HTTP/WebSocket 与构建/类型检查验证，未做截图走查 |
| 登录状态 | 采用 localStorage 存 JWT（第七阶段评估改为 HttpOnly Cookie） |
| 工作目录 | 目前仅平台托管目录；指定用户项目目录（白名单 + 审批）在第二阶段随审批机制实现 |
| 认证状态检测 | Claude Code 连接测试只检测 CLI 版本，登录态在首次真实任务时体现 |

## 5. 启动与访问

```powershell
.\start.ps1          # http://127.0.0.1:8000
.\start.ps1 -Dev     # http://127.0.0.1:5173（热更新）
```

首次访问创建管理员 → Agent 管理 → 接入 Agent（推荐：Claude Code 模型 `haiku`；OpenCode 模型 `opencode/big-pickle`）→ 测试连接 → 任务中心发送任务。

## 6. 下一阶段计划（第二阶段：群聊与任务协作）

1. 引入 Alembic，新增 conversations / conversation_members / messages / handoffs / approvals 表。
2. 群聊界面（左侧群列表、中间消息流、右侧成员与任务面板），@Agent / @全体解析为结构化任务。
3. 人工分工模式：执行 / 分析 / 审查 / 汇总角色，按依赖顺序串行或并行。
4. 自动协作模式：主控 Agent 输出 JSON 计划（平台用 Schema 校验后才落为子任务），子任务共享父 trace_id。
5. Handoff 结构化记录与接收确认；任务暂停 / 恢复（基于会话续接 `--resume` / `--session`）。
6. 审批：高风险工具调用与用户项目目录访问进入 `waiting_approval`。
