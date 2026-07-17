"""LLM Gateway configuration and model registry.

All LLM calls in this project route through the LangSmith LLM Gateway
(gateway.smith.langchain.com). The gateway authenticates with a LangSmith API
key and resolves the real provider keys from workspace secrets, so no provider
API keys are needed locally.

This module centralises:
  - The gateway base URL and per-provider paths
  - The list of models available through the gateway (served to the frontend
    via GET /api/models)
  - Factory function for BaseChatModel (used by deepagents / init_chat_model
    and all direct LLM calls)
"""

import os

from langchain.chat_models import init_chat_model
from langchain_core.language_models import BaseChatModel

# ---------------------------------------------------------------------------
# Gateway configuration
# ---------------------------------------------------------------------------

GATEWAY_BASE_URL = os.environ.get(
    "LANGSMITH_GATEWAY_URL", "https://gateway.smith.langchain.com"
)

# Per-provider gateway paths (appended to GATEWAY_BASE_URL).
_PROVIDER_PATHS = {
    "anthropic": "/anthropic",
    "openai": "/openai/v1",
    "google": "/gemini",
    "fireworks": "/fireworks",
    "baseten": "/baseten/v1",
}

# Maps our provider key to the model_provider string that init_chat_model expects.
_PROVIDER_TO_LANGCHAIN = {
    "anthropic": "anthropic",
    "openai": "openai",
    "google": "google_genai",
    "fireworks": "fireworks",
    "baseten": "baseten",
}

# API key used for gateway authentication.
# In production (LSD), only LANGSMITH_API_KEY is set. In local dev, start.sh
# sets the per-provider *_API_KEY env vars from LANGSMITH_API_KEY, and the
# shell may also export ANTHROPIC_API_KEY (gateway coding agents setup).
# We check LANGSMITH_API_KEY first so a stale direct provider key can't
# shadow the gateway key in production.
_API_KEY = (
    os.environ.get("LANGSMITH_API_KEY")
    or os.environ.get("ANTHROPIC_API_KEY")
    or os.environ.get("OPENAI_API_KEY")
    or os.environ.get("FIREWORKS_API_KEY")
    or os.environ.get("BASETEN_API_KEY")
    or ""
)

# ---------------------------------------------------------------------------
# Model registry
# ---------------------------------------------------------------------------

# Each entry: {id, label, provider, model}
# The gateway resolves provider credentials from workspace secrets, so we only
# need the provider name (to pick the right gateway path + SDK) and the model
# identifier that the upstream provider expects.
AVAILABLE_MODELS: list[dict[str, str]] = [
    # ── Fireworks ────────────────────────────────────────────────────────────
    {
        "id": "fireworks:accounts/fireworks/models/glm-5p2",
        "label": "GLM 5.2",
        "provider": "fireworks",
        "model": "accounts/fireworks/models/glm-5p2",
    },
    {
        "id": "fireworks:accounts/fireworks/models/glm-5p1",
        "label": "GLM 5.1",
        "provider": "fireworks",
        "model": "accounts/fireworks/models/glm-5p1",
    },
    {
        "id": "fireworks:accounts/fireworks/models/deepseek-v4-pro",
        "label": "DeepSeek V4 Pro",
        "provider": "fireworks",
        "model": "accounts/fireworks/models/deepseek-v4-pro",
    },
    {
        "id": "fireworks:accounts/fireworks/models/gpt-oss-120b",
        "label": "GPT-OSS 120B",
        "provider": "fireworks",
        "model": "accounts/fireworks/models/gpt-oss-120b",
    },
    {
        "id": "fireworks:accounts/fireworks/models/kimi-k2p6",
        "label": "Kimi K2.6",
        "provider": "fireworks",
        "model": "accounts/fireworks/models/kimi-k2p6",
    },
    # ── Anthropic ────────────────────────────────────────────────────────────
    {
        "id": "anthropic:claude-sonnet-4-6",
        "label": "Claude Sonnet 4.6",
        "provider": "anthropic",
        "model": "claude-sonnet-4-6",
    },
    {
        "id": "anthropic:claude-opus-4-6",
        "label": "Claude Opus 4.6",
        "provider": "anthropic",
        "model": "claude-opus-4-6",
    },
    {
        "id": "anthropic:claude-haiku-4-5-20251001",
        "label": "Claude Haiku 4.5",
        "provider": "anthropic",
        "model": "claude-haiku-4-5-20251001",
    },
    # ── OpenAI ───────────────────────────────────────────────────────────────
    {
        "id": "openai:gpt-5.2",
        "label": "GPT-5.2",
        "provider": "openai",
        "model": "gpt-5.2",
    },
    {
        "id": "openai:gpt-5.1",
        "label": "GPT-5.1",
        "provider": "openai",
        "model": "gpt-5.1",
    },
    {
        "id": "openai:gpt-5",
        "label": "GPT-5",
        "provider": "openai",
        "model": "gpt-5",
    },
    {
        "id": "openai:gpt-5-mini",
        "label": "GPT-5 Mini",
        "provider": "openai",
        "model": "gpt-5-mini",
    },
    {
        "id": "openai:gpt-4o",
        "label": "GPT-4o",
        "provider": "openai",
        "model": "gpt-4o",
    },
    {
        "id": "openai:gpt-4o-mini",
        "label": "GPT-4o Mini",
        "provider": "openai",
        "model": "gpt-4o-mini",
    },
    {
        "id": "openai:o3",
        "label": "o3",
        "provider": "openai",
        "model": "o3",
    },
    {
        "id": "openai:o3-mini",
        "label": "o3-mini",
        "provider": "openai",
        "model": "o3-mini",
    },
    {
        "id": "openai:o4-mini",
        "label": "o4-mini",
        "provider": "openai",
        "model": "o4-mini",
    },
    # ── Google ───────────────────────────────────────────────────────────────
    {
        "id": "google:gemini-3.5-flash",
        "label": "Gemini 3.5 Flash",
        "provider": "google",
        "model": "gemini-3.5-flash",
    },
    {
        "id": "google:gemini-3.1-pro-preview",
        "label": "Gemini 3.1 Pro",
        "provider": "google",
        "model": "gemini-3.1-pro-preview",
    },
    {
        "id": "google:gemini-3.1-flash-lite",
        "label": "Gemini 3.1 Flash Lite",
        "provider": "google",
        "model": "gemini-3.1-flash-lite",
    },
    {
        "id": "google:gemini-2.5-pro",
        "label": "Gemini 2.5 Pro",
        "provider": "google",
        "model": "gemini-2.5-pro",
    },
    {
        "id": "google:gemini-2.5-flash",
        "label": "Gemini 2.5 Flash",
        "provider": "google",
        "model": "gemini-2.5-flash",
    },
    # ── Baseten ──────────────────────────────────────────────────────────────
    {
        "id": "baseten:zai-org/GLM-5.2",
        "label": "GLM 5.2",
        "provider": "baseten",
        "model": "zai-org/GLM-5.2",
    },
    {
        "id": "baseten:moonshotai/Kimi-K2.7-Code",
        "label": "Kimi K2.7 Code",
        "provider": "baseten",
        "model": "moonshotai/Kimi-K2.7-Code",
    },
    {
        "id": "baseten:nvidia/NVIDIA-Nemotron-3-Ultra-550B-A55B",
        "label": "Nemotron 3 Ultra 550B",
        "provider": "baseten",
        "model": "nvidia/NVIDIA-Nemotron-3-Ultra-550B-A55B",
    },
]

DEFAULT_MODEL_ID = AVAILABLE_MODELS[0]["id"]

_MODEL_MAP: dict[str, dict[str, str]] = {m["id"]: m for m in AVAILABLE_MODELS}


def get_model_info(model_id: str) -> dict[str, str] | None:
    """Return the registry entry for a model ID, or None if not found."""
    return _MODEL_MAP.get(model_id)


def _provider_base_url(provider: str) -> str:
    """Return the gateway URL for a provider."""
    path = _PROVIDER_PATHS.get(provider)
    if not path:
        raise ValueError(f"Unknown provider: {provider}")
    return f"{GATEWAY_BASE_URL}{path}"


# ---------------------------------------------------------------------------
# Factory: BaseChatModel (for deepagents / LangChain / all LLM calls)
# ---------------------------------------------------------------------------

def get_chat_model(model_id: str) -> BaseChatModel:
    """Return a BaseChatModel configured to route through the gateway.

    Accepts either a registry ID (e.g. 'anthropic:claude-sonnet-4-6') or a
    raw model string in provider:model format. The model is initialised with
    explicit base_url and api_key so it always goes through the gateway
    regardless of what env vars are set.
    """
    info = get_model_info(model_id)
    if info:
        provider = info["provider"]
        model = info["model"]
    elif ":" in model_id:
        provider, model = model_id.split(":", 1)
    else:
        # Default to anthropic for bare model names (backwards compat).
        provider = "anthropic"
        model = model_id

    base_url = _provider_base_url(provider)
    lc_provider = _PROVIDER_TO_LANGCHAIN.get(provider, provider)

    return init_chat_model(
        model=model,
        model_provider=lc_provider,
        base_url=base_url,
        api_key=_API_KEY,
    )
