# 05 · 技术栈重构：LangChain 编排 + Vue 3 前端

本次重构把两条主线换成更明确的形态，功能面保持不变：

1. **后端**：保留 Claude Code / OpenCode / Codex / HTTP 四类适配器，但把协作编排与模型调用收进 LangChain（`create_agent` + `response_format` 结构化输出 + 官方 usage 回调）；
2. **前端**：从 React 18 + Ant Design 5 整体重写为 Vue 3 + TypeScript + Vite + Ant Design Vue 4。

## 1. 后端：编排层迁到 LangChain（1.4，按官方文档写法）

### 1.1 新增 `backend/app/chains/`

| 文件 | 职责 |
| --- | --- |
| `llm.py` | `init_chat_model("provider:model", ...)` 统一构造（`langchain-deepseek` / `langchain-openai` / `langchain-anthropic`）；提供 `llm_available()` / `orchestrator_model()`，未配置密钥时返回 `None` |
| `schemas.py` | `Plan` / `PlannedStep`（Pydantic），作为 `with_structured_output` 的 schema |
| `prompts.py` | `PLAN_PROMPT`、`SUMMARY_PROMPT`（`ChatPromptTemplate`），以及成员名单渲染 |
| `planner.py` | `create_agent(model, tools=[], system_prompt=..., response_format=Plan)`，从 `structured_response` 取计划，再经 `validate_plan` 校验 |
| `summarizer.py` | `create_agent(model, tools=[], system_prompt=...)`，取最后一条 AI 消息 |
| `plan.py` | 计划校验：成员存在性、id 唯一、依赖成环；旧 JSON 解析路径复用同一份规则 |
| `callbacks.py` | `UsageMetadataCallbackHandler`（官方 token 统计）+ `PlatformCallbackHandler`（`on_llm_*` / `on_tool_*` 投影为平台事件） |
| `runnable.py` | `AdapterRunnable`：任意 `AgentAdapter` 的 `Runnable` 包装，`run_adapter()` 为统一入口 |

### 1.1.1 对齐的官方 API（LangChain 1.4）

| 用法 | 出处 |
| --- | --- |
| `from langchain.chat_models import init_chat_model` → `init_chat_model("deepseek:deepseek-chat", api_key=..., base_url=...)` | Models → Initialize a model |
| `from langchain.agents import create_agent` → `create_agent(model=..., tools=[], system_prompt=..., response_format=Plan)` | Overview → Create an agent；Structured output → Response format |
| 结果取 `result["structured_response"]`（计划）与 `result["messages"][-1]`（汇总） | Structured output / Agents |
| `from langchain.messages import HumanMessage, AIMessage` | Messages |
| `from langchain_core.callbacks import UsageMetadataCallbackHandler` 统计 token | Models → Token usage |
| `await agent.ainvoke({"messages": [...]}, config={"callbacks": [...]})` | Models → Invoke / Invocation config |

官方明确说明：`create_agent` 的结构化输出带校验失败重试，而 `prompt | model | parser` 这类链没有；因此规划与汇总都走 agent 形态，而不是手写链。

### 1.2 行为变化

- 「自动协作」的**规划**与**汇总**不再派给主控 Agent 跑子任务，而是由平台自己的 LangChain Agent 完成；链式调用同样写 `AgentRun`（含 token / 模型 / 耗时），界面上与外部 Agent 一致。
- 未配置 `AGENTOPS_ORCHESTRATOR_*` 或缺少密钥时，自动退回旧路径（主控 Agent 出计划 → `parse_plan` 解析 JSON），因此离线测试与旧部署不受影响。
- 所有 Agent 执行从 `adapter.run(...)` 收敛到 `run_adapter(...)` → `AdapterRunnable.ainvoke(...)`，适配器只关心自己的流协议。
- 计划校验统一到 `chains/plan.py`，`services/collab.py` 的 `parse_plan` 变成薄封装，错误信息与链式路径保持一致。

### 1.3 顺带修复

- `http_agent._client` 在 `NO_PROXY` 含带方括号的 IPv6（如 `[::1]`）时，httpx 会把该项解析成非法 URL 模式 `*[::1]` 并在建连前抛 `InvalidURL`。现在构造客户端时会临时去掉方括号（`::1` 覆盖同一主机），构建后再恢复环境变量。
- 后端渲染 SPA 外壳（`_render_shell`）的挂载点从 `#root` 改为 `#app`，与 Vue 的 `index.html` 保持一致。

## 2. 前端：Vue 3 重写

### 2.1 结构

```text
frontend/src/
├─ main.ts                  # createApp + antd + router
├─ App.vue                  # a-config-provider(zhCN) + a-app + router-view
├─ router/index.ts          # 路由表与登录守卫（bootstrap 一次会话恢复）
├─ api.ts                   # fetch 封装、JWT、错误归一化、全部 TS 类型
├─ auth.ts                  # 轻量 reactive 登录态（无 Pinia）
├─ live.ts                  # 全局共享 WebSocket，useLive / useLiveStatus
├─ labels.ts                # 状态、能力、事件标签与时间/耗时/金额格式化
├─ components/              # AppLayout、StatusTag、LevelTag、EventTag
└─ pages/                   # Login、Dashboard、Agents、Tasks、TaskDetail、Chat、Monitor、Settings、Placeholder
```

### 2.2 技术选择

- **组件库**：Ant Design Vue 4，保持原 AntD 的组件语义（`a-table` + `#bodyCell`、`a-drawer`、`a-form` 规则校验、`a-descriptions` 等），迁移时页面结构与交互尽量一比一。
- **图表**：`echarts` 直接挂 DOM（`onMounted` 初始化、`resize` 自适应、`onBeforeUnmount` dispose），替代 React 的 `@ant-design/charts`。
- **实时**：沿用原设计——单例 WebSocket `/api/ws?token=...`，按 `topic` 分发（`task_updated`、`run_updated`、`trace_event`、`message_created`、`collab_updated`、`agent_updated`），断线 2s 重连；聊天页另有 5s 轮询兜底。
- **权限**：`auth.can('operator' | 'admin')` 控制写操作按钮，viewer 只读。

### 2.3 验证

- `npx vue-tsc --noEmit`：0 错误（`strict` + `noUnusedLocals`）。
- `npm run build`：产出 `dist/` 与 `.vite/manifest.json`，后端 `_render_shell` 直接消费该 manifest。
- 后端 `uv run pytest`：57 个用例通过（其中 `tests/test_chains.py` 覆盖计划校验、结构化输出链、汇总链与 `AdapterRunnable`，全部离线）。

## 3. 已知差异

- TaskDetail 保留了任务、run、事件时间线的全部能力，但原页面里的 `CollabCard`（协作面板）未移植——协作面板目前只存在于 Chat 页，后续抽成共享组件后再接入。
- Monitor 的事件详情抽屉、Chat 的交接/步骤抽屉均为页面内实现，尚未抽成公共组件。
- Dashboard 图表按当前浅色主题配色（原 React 版是 AntD darkAlgorithm），系列色沿用原校验过的三色。
