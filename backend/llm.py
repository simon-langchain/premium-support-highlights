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

import logging
import os

from langchain.chat_models import init_chat_model
from langchain_core.language_models import BaseChatModel

_log = logging.getLogger(__name__)

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
# In production (LSD), set LLM_GATEWAY_API_KEY (LANGSMITH_API_KEY is reserved
# by LSD for its own use). In local dev, start.sh sets the per-provider
# *_API_KEY env vars from the gateway key, and the shell may also export
# ANTHROPIC_API_KEY (gateway coding agents setup).
_API_KEY = (
    os.environ.get("LLM_GATEWAY_API_KEY")
    or os.environ.get("LANGSMITH_API_KEY")
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
    # The frontend preselects the first entry, so keep DEFAULT_MODEL_ID first.
    # ── Anthropic ────────────────────────────────────────────────────────────
    {"id": "anthropic:claude-sonnet-5", "label": "Claude Sonnet 5", "provider": "anthropic", "model": "claude-sonnet-5"},
    {"id": "anthropic:claude-opus-5-5", "label": "Claude Opus 5.5", "provider": "anthropic", "model": "claude-opus-5-5"},
    {"id": "anthropic:claude-fable-5-1", "label": "Claude Fable 5.1", "provider": "anthropic", "model": "claude-fable-5-1"},
    {"id": "anthropic:claude-haiku-4-5-20251001", "label": "Claude Haiku 4.5", "provider": "anthropic", "model": "claude-haiku-4-5-20251001"},
    # ── OpenAI (Responses API — see get_chat_model) ──────────────────────────
    {"id": "openai:gpt-6-sol", "label": "GPT-6 Sol", "provider": "openai", "model": "gpt-6-sol"},
    {"id": "openai:gpt-6-luna", "label": "GPT-6 Luna", "provider": "openai", "model": "gpt-6-luna"},
    # ── Google ───────────────────────────────────────────────────────────────
    {"id": "google:gemini-3.8-flash", "label": "Gemini 3.8 Flash", "provider": "google", "model": "gemini-3.8-flash"},
    {"id": "google:gemini-3.1-pro-preview", "label": "Gemini 3.1 Pro", "provider": "google", "model": "gemini-3.1-pro-preview"},
    {"id": "google:gemini-3.5-flash-lite", "label": "Gemini 3.5 Flash Lite", "provider": "google", "model": "gemini-3.5-flash-lite"},
    # ── Fireworks ────────────────────────────────────────────────────────────
    {"id": "fireworks:accounts/fireworks/models/glm-5p3", "label": "GLM 5.3", "provider": "fireworks", "model": "accounts/fireworks/models/glm-5p3"},
    {"id": "fireworks:accounts/fireworks/models/glm-5p3-flash", "label": "GLM 5.3 Flash", "provider": "fireworks", "model": "accounts/fireworks/models/glm-5p3-flash"},
    {"id": "fireworks:accounts/fireworks/models/kimi-k3", "label": "Kimi K3", "provider": "fireworks", "model": "accounts/fireworks/models/kimi-k3"},
    {"id": "fireworks:accounts/fireworks/models/minimax-m3", "label": "MiniMax M3", "provider": "fireworks", "model": "accounts/fireworks/models/minimax-m3"},
    {"id": "fireworks:accounts/fireworks/models/deepseek-v4-pro-0813", "label": "DeepSeek V4 Pro", "provider": "fireworks", "model": "accounts/fireworks/models/deepseek-v4-pro-0813"},
    # ── Baseten ──────────────────────────────────────────────────────────────
    {"id": "baseten:zai-org/GLM-5.3", "label": "GLM 5.3", "provider": "baseten", "model": "zai-org/GLM-5.3"},
    {"id": "baseten:moonshotai/Kimi-K3", "label": "Kimi K3", "provider": "baseten", "model": "moonshotai/Kimi-K3"},
]

DEFAULT_MODEL_ID = "anthropic:claude-sonnet-5"
assert AVAILABLE_MODELS[0]["id"] == DEFAULT_MODEL_ID, "keep the default first (the frontend preselects it)"

# Model IDs the provider no longer serves -> replacement. Saved schedules and caches keep
# old IDs, so remap rather than fail. Models merely dropped from the list above still
# work (get_chat_model accepts any provider:model) and need no entry here.
_RETIRED_MODELS: dict[str, str] = {
    "fireworks:accounts/fireworks/models/glm-5p1": "fireworks:accounts/fireworks/models/glm-5p3",
    "fireworks:accounts/fireworks/models/deepseek-v4-pro": "fireworks:accounts/fireworks/models/deepseek-v4-pro-0813",
}

_MODEL_MAP: dict[str, dict[str, str]] = {m["id"]: m for m in AVAILABLE_MODELS}


def get_model_info(model_id: str) -> dict[str, str] | None:
    """Return the registry entry for a model ID, or None if not found."""
    return _MODEL_MAP.get(model_id)


# Listed models that reject a `temperature` parameter (400 "temperature is deprecated").
# check_models.py fails if this list doesn't match what the providers accept.
_NO_TEMPERATURE = frozenset({
    "anthropic:claude-sonnet-5",
    "anthropic:claude-opus-5-5",
    "anthropic:claude-fable-5-1",
    "openai:gpt-6-sol",
    "openai:gpt-6-luna",
})


def supports_temperature(model_id: str) -> bool:
    """False for models known to reject `temperature`; unlisted models are assumed to accept it."""
    return model_id not in _NO_TEMPERATURE


def _provider_base_url(provider: str) -> str:
    """Return the gateway URL for a provider."""
    path = _PROVIDER_PATHS.get(provider)
    if not path:
        raise ValueError(f"Unknown provider: {provider}")
    return f"{GATEWAY_BASE_URL}{path}"


# ---------------------------------------------------------------------------
# Factory: BaseChatModel (for deepagents / LangChain / all LLM calls)
# ---------------------------------------------------------------------------

_chat_model_cache: dict[str, BaseChatModel] = {}


def get_chat_model(model_id: str) -> BaseChatModel:
    """Return a BaseChatModel configured to route through the gateway.

    Accepts either a registry ID (e.g. 'anthropic:claude-sonnet-4-6') or a
    raw model string in provider:model format. The model is initialised with
    explicit base_url and api_key so it always goes through the gateway
    regardless of what env vars are set.

    Cached per model_id and reused — this is called once per ticket/insight
    generation (potentially dozens of times per account), and each fresh
    init_chat_model() call constructs its own underlying async HTTP client
    that's never explicitly closed, showing up as "Unclosed connector"
    warnings once garbage-collected. Chat model instances are safe to share
    across concurrent calls, so caching avoids the churn entirely.
    """
    if model_id in _RETIRED_MODELS:
        _log.warning("Model %s is retired; using %s", model_id, _RETIRED_MODELS[model_id])
        model_id = _RETIRED_MODELS[model_id]
    if model_id in _chat_model_cache:
        return _chat_model_cache[model_id]

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

    chat_model = init_chat_model(
        model=model,
        model_provider=lc_provider,
        base_url=base_url,
        api_key=_API_KEY,
        # GPT-6 models only accept tools alongside reasoning via the Responses API
        **({"use_responses_api": True} if provider == "openai" else {}),
        # langchain-anthropic defaults to 4096, which Claude's built-in thinking can use up
        # before writing any text. Kept below the SDK's non-streaming long-request limit.
        **({"max_tokens": 16000} if provider == "anthropic" else {}),
    )
    if info:  # only cache listed models: model_id can come from a request body
        _chat_model_cache[model_id] = chat_model
    return chat_model
