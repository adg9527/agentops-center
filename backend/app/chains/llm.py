"""Chat-model factory built on LangChain's `init_chat_model`.

Follows the documented provider/model string convention (`"provider:model"`,
https://docs.langchain.com/oss/python/langchain/models): one call site decides
which provider to use, and LangChain resolves the integration package.
"""

from __future__ import annotations

import logging
import os
from functools import lru_cache

from langchain.chat_models import init_chat_model
from langchain_core.language_models import BaseChatModel

from ..config import get_settings

log = logging.getLogger(__name__)

DEFAULT_MODELS = {
    "deepseek": "deepseek-chat",
    "openai": "gpt-4o-mini",
    "anthropic": "claude-3-5-sonnet-latest",
}
SUPPORTED_PROVIDERS = tuple(DEFAULT_MODELS)


def build_chat_model(
    *,
    provider: str = "deepseek",
    model: str | None = None,
    api_key: str | None = None,
    base_url: str | None = None,
    temperature: float = 0.0,
    timeout: float = 180.0,
    max_retries: int = 2,
    **model_kwargs: object,
) -> BaseChatModel:
    """Build a chat model via `init_chat_model("provider:model", ...)`.

    `deepseek`, `openai` and `anthropic` map to their LangChain integration
    packages (`langchain-deepseek`, `langchain-openai`, `langchain-anthropic`);
    `base_url` supports OpenAI-compatible gateways without a new provider.
    """
    provider = (provider or "deepseek").strip().lower()
    if provider not in DEFAULT_MODELS:
        raise ValueError(f"未知的模型 provider: {provider}（可选: {', '.join(SUPPORTED_PROVIDERS)}）")
    model = model or DEFAULT_MODELS[provider]
    if not api_key:
        raise ValueError(f"provider {provider} 缺少 API Key")

    kwargs: dict[str, object] = {
        "api_key": api_key,
        "temperature": temperature,
        "timeout": timeout,
        "max_retries": max_retries,
    }
    if base_url:
        kwargs["base_url"] = base_url
    # e.g. `http_async_client` for a loopback endpoint that must bypass a system proxy
    kwargs.update(model_kwargs)
    return init_chat_model(f"{provider}:{model}", **kwargs)


def resolve_api_key(env_name: str | None = None, inline: str | None = None) -> str | None:
    """Explicit key first, then the configured environment variable."""
    if inline:
        return inline
    if env_name:
        return os.environ.get(env_name) or None
    return None


@lru_cache(maxsize=4)
def _env_key(env_name: str) -> str | None:
    return os.environ.get(env_name) or None


def llm_available() -> bool:
    """True when the orchestration agents can reach a model in this deployment."""
    s = get_settings()
    if s.orchestrator_api_key:
        return True
    return bool(_env_key(s.orchestrator_api_key_env))


def orchestrator_model() -> BaseChatModel | None:
    """The model used by the planning/summarising agents, or None when unconfigured.

    Returning None (instead of raising) is deliberate: a deployment without an
    orchestration key keeps working through the agent-driven coordinator path.
    """
    s = get_settings()
    key = s.orchestrator_api_key or _env_key(s.orchestrator_api_key_env)
    if not key:
        return None
    return build_chat_model(
        provider=s.orchestrator_provider,
        model=s.orchestrator_model,
        api_key=key,
        base_url=s.orchestrator_base_url,
        temperature=s.orchestrator_temperature,
    )
