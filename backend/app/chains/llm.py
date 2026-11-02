"""Chat-model factory.

One place decides how a provider name maps to a LangChain chat model, so the
native agent adapter and the orchestration chains never duplicate that logic.
"""

from __future__ import annotations

import logging
import os
from functools import lru_cache

from langchain_anthropic import ChatAnthropic
from langchain_core.language_models import BaseChatModel
from langchain_openai import ChatOpenAI

from ..config import get_settings

log = logging.getLogger(__name__)

DEEPSEEK_BASE_URL = "https://api.deepseek.com/v1"
DEFAULT_MODELS = {
    "deepseek": "deepseek-chat",
    "openai": "gpt-4o-mini",
    "anthropic": "claude-3-5-sonnet-latest",
}


def build_chat_model(
    *,
    provider: str = "deepseek",
    model: str | None = None,
    api_key: str | None = None,
    base_url: str | None = None,
    temperature: float = 0.0,
    timeout: float = 180.0,
    max_retries: int = 2,
) -> BaseChatModel:
    """Build a chat model for `provider`.

    `deepseek` and any OpenAI-compatible gateway use `ChatOpenAI` with a base URL;
    `anthropic` uses `ChatAnthropic`. Raises `ValueError` when the provider is
    unknown or no API key is available.
    """
    provider = (provider or "deepseek").strip().lower()
    model = model or DEFAULT_MODELS.get(provider)
    if not model:
        raise ValueError(f"未知的模型 provider: {provider}")

    if provider == "anthropic":
        return ChatAnthropic(  # type: ignore[call-arg]
            model=model,
            api_key=api_key,  # type: ignore[arg-type]
            base_url=base_url or None,
            temperature=temperature,
            timeout=timeout,
            max_retries=max_retries,
        )

    if not api_key:
        raise ValueError(f"provider {provider} 缺少 API Key")

    if provider == "deepseek":
        base_url = base_url or DEEPSEEK_BASE_URL
    return ChatOpenAI(
        model=model,
        api_key=api_key,  # type: ignore[arg-type]
        base_url=base_url or None,
        temperature=temperature,
        timeout=timeout,
        max_retries=max_retries,
    )


def resolve_api_key(env_name: str | None = None, inline: str | None = None) -> str | None:
    """Environment variable first, then an explicitly configured key."""
    if inline:
        return inline
    if env_name:
        return os.environ.get(env_name) or None
    return None


@lru_cache(maxsize=4)
def _orchestrator_key(env_name: str) -> str | None:
    return os.environ.get(env_name) or None


def llm_available() -> bool:
    """True when the orchestration chains can reach a model in this deployment."""
    s = get_settings()
    if s.orchestrator_api_key:
        return True
    return bool(_orchestrator_key(s.orchestrator_api_key_env))


def orchestrator_model() -> BaseChatModel | None:
    """The model used by the planning/summarising chains, or None when unconfigured.

    Returning None (instead of raising) is deliberate: a deployment without an
    orchestration key keeps working through the agent-driven coordinator path.
    """
    s = get_settings()
    key = s.orchestrator_api_key or _orchestrator_key(s.orchestrator_api_key_env)
    if not key:
        return None
    return build_chat_model(
        provider=s.orchestrator_provider,
        model=s.orchestrator_model,
        api_key=key,
        base_url=s.orchestrator_base_url,
        temperature=s.orchestrator_temperature,
    )
