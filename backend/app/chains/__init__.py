"""LangChain composition layer.

Everything that talks to a model or composes an agent lives here, so the FastAPI
services stay thin. Written against the current LangChain 1.x API
(https://docs.langchain.com/oss/python/langchain/overview):

* `llm`        — `init_chat_model("provider:model", ...)` factory (DeepSeek / OpenAI / Anthropic)
* `planner`    — `create_agent(..., response_format=Plan)`, i.e. structured output with retries
* `summarizer` — plain `create_agent` whose last AI message is the report
* `callbacks`  — `UsageMetadataCallbackHandler` for tokens + a handler projecting trace events
* `runnable`   — LangChain `Runnable` wrapper around any AgentAdapter
* `plan`       — validation/normalisation shared by the agent and the legacy JSON parser

Import the submodules you need (`app.chains.planner`, ...). The package itself
imports nothing, so `app.adapters` and `app.chains` can reference each other
without an import cycle.
"""
