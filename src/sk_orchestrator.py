"""
Semantic Kernel orchestration layer.

Why this file exists
--------------------
The architecture document and the Zenn article describe the system as a
Semantic-Kernel-powered Multi-Agent orchestrator. This module turns that
claim into reality: it boots a `semantic_kernel.Kernel` with an
`AzureChatCompletion` service and exposes a synchronous `complete()` that
the existing `LLMClient` can delegate to.

Design choices
--------------
- Kept synchronous on the surface so `src/agents.py` does not have to
  become async. SK is natively async, so we wrap with `asyncio.run()`
  (with a `nest_asyncio` fallback when an event loop is already
  running — Streamlit's runtime can hold one).
- Only the **text** completions go through SK. Vision (image input) is
  still served by the raw `AzureOpenAI` client in `src/agents.py:LLMClient`
  because SK's image-content surface is more cumbersome and brings no
  evaluation upside.
- The kernel is built lazily and cached. The first text agent call pays
  the cold-start cost (~50 ms); subsequent calls reuse the kernel.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Optional

from .utils import load_env
import os


_KERNEL_CACHE: dict[str, object] = {}


@dataclass
class SKConfig:
    endpoint: str
    api_key: str
    deployment: str
    api_version: str

    @classmethod
    def from_env(cls) -> Optional["SKConfig"]:
        load_env()
        ep = os.getenv("AZURE_OPENAI_ENDPOINT")
        key = os.getenv("AZURE_OPENAI_API_KEY")
        dep = os.getenv("AZURE_OPENAI_DEPLOYMENT")
        ver = os.getenv("AZURE_OPENAI_API_VERSION", "2024-08-01-preview")
        if not (ep and key and dep):
            return None
        return cls(endpoint=ep, api_key=key, deployment=dep, api_version=ver)


def _build_kernel(cfg: SKConfig):
    """Build a Semantic Kernel and register the Azure OpenAI service."""
    from semantic_kernel import Kernel
    from semantic_kernel.connectors.ai.open_ai import AzureChatCompletion

    kernel = Kernel()
    service = AzureChatCompletion(
        service_id="edgeops_chat",
        deployment_name=cfg.deployment,
        endpoint=cfg.endpoint,
        api_key=cfg.api_key,
        api_version=cfg.api_version,
    )
    kernel.add_service(service)
    return kernel, service


def _get_kernel():
    if "kernel" in _KERNEL_CACHE:
        return _KERNEL_CACHE["kernel"], _KERNEL_CACHE["service"]
    cfg = SKConfig.from_env()
    if cfg is None:
        return None, None
    kernel, service = _build_kernel(cfg)
    _KERNEL_CACHE["kernel"] = kernel
    _KERNEL_CACHE["service"] = service
    return kernel, service


async def _complete_async(system: str, user: str, *, temperature: float, json_mode: bool) -> str:
    from semantic_kernel.contents.chat_history import ChatHistory
    from semantic_kernel.connectors.ai.open_ai import OpenAIChatPromptExecutionSettings

    _, service = _get_kernel()
    if service is None:
        raise RuntimeError("Semantic Kernel not configured (Azure OpenAI env missing).")

    history = ChatHistory()
    history.add_system_message(system)
    history.add_user_message(user)

    settings = OpenAIChatPromptExecutionSettings(
        service_id="edgeops_chat",
        temperature=temperature,
    )
    if json_mode:
        # SK passes this through to Azure OpenAI's response_format.
        settings.response_format = {"type": "json_object"}

    result = await service.get_chat_message_content(chat_history=history, settings=settings)
    if result is None:
        return ""
    return str(result)


def complete(system: str, user: str, *, temperature: float = 0.2, json_mode: bool = True) -> str:
    """Synchronous wrapper. Runs the SK call in an event loop and returns the text."""
    try:
        return asyncio.run(_complete_async(system, user, temperature=temperature, json_mode=json_mode))
    except RuntimeError as exc:
        # An event loop is already running (e.g. inside some Streamlit contexts).
        if "already running" not in str(exc).lower() and "cannot be called" not in str(exc).lower():
            raise
        import nest_asyncio  # type: ignore
        nest_asyncio.apply()
        loop = asyncio.get_event_loop()
        return loop.run_until_complete(
            _complete_async(system, user, temperature=temperature, json_mode=json_mode)
        )


def is_available() -> bool:
    """Cheap probe — returns True if SK can be built from current env."""
    return SKConfig.from_env() is not None
