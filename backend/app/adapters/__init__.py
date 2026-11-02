from .base import AgentAdapter
from .claude_code import ClaudeCodeAdapter
from .claude_sdk import ClaudeSdkAdapter
from .codex import CodexAdapter
from .http_agent import HttpAgentAdapter
from .langchain_agent import LangChainAdapter
from .opencode import OpenCodeAdapter

ADAPTERS: dict[str, AgentAdapter] = {
    a.type: a for a in (ClaudeCodeAdapter(), ClaudeSdkAdapter(), OpenCodeAdapter(), LangChainAdapter(), CodexAdapter(), HttpAgentAdapter())
}


def get_adapter(adapter_type: str) -> AgentAdapter:
    try:
        return ADAPTERS[adapter_type]
    except KeyError:
        raise ValueError(f"unknown adapter type: {adapter_type}") from None
