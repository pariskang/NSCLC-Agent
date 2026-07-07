"""Factory that builds a provider instance from a config dict."""

from __future__ import annotations

import os
from typing import Any, Optional

from .base import GenerationParams, LLMProvider, ProviderError
from .azure import AzureOpenAIProvider
from .litellm_provider import LiteLLMProvider
from .minimax import MiniMaxProvider
from .mock import MockProvider
from .poe import PoeProvider

KNOWN_KINDS = ("litellm", "azure", "poe", "minimax", "mock")


def _resolve_secret(cfg: dict, key_field: str, env_field: str) -> Optional[str]:
    """Resolve a secret: inline value, or the named environment variable."""
    if cfg.get(key_field):
        val = str(cfg[key_field])
        if val.startswith("${") and val.endswith("}"):
            return os.environ.get(val[2:-1], "")
        return val
    env_name = cfg.get(env_field)
    if env_name:
        return os.environ.get(env_name, "")
    return None


def _gen_params(cfg: dict, defaults: GenerationParams) -> GenerationParams:
    gen = cfg.get("generation", {}) or {}
    return defaults.merged(
        temperature=gen.get("temperature"),
        max_tokens=gen.get("max_tokens"),
        top_p=gen.get("top_p"),
        extra=gen.get("extra"),
    )


def build_provider(
    name: str,
    cfg: dict[str, Any],
    *,
    defaults: Optional[GenerationParams] = None,
) -> LLMProvider:
    """Instantiate a provider from its configuration block."""
    defaults = defaults or GenerationParams()
    kind = (cfg.get("type") or cfg.get("kind") or "").lower()
    if not kind:
        raise ProviderError(f"Provider {name!r} is missing a 'type' field")
    params = _gen_params(cfg, defaults)

    vision = bool(cfg.get("vision", False))

    if kind == "mock":
        return MockProvider(name=name, model=cfg.get("model", "mock-echo"),
                            params=params, supports_vision=vision)

    if kind == "litellm":
        return LiteLLMProvider(
            name, cfg.get("model", "gpt-4o"), params,
            api_key=_resolve_secret(cfg, "api_key", "api_key_env"),
            api_base=cfg.get("api_base"),
            extra_kwargs=cfg.get("extra_kwargs"),
            supports_vision=vision,
        )

    if kind == "azure":
        return AzureOpenAIProvider(
            name, cfg.get("deployment") or cfg.get("model", ""), params,
            api_key=_resolve_secret(cfg, "api_key", "api_key_env") or "",
            endpoint=cfg.get("endpoint", ""),
            api_version=cfg.get("api_version", "2024-10-21"),
            timeout=float(cfg.get("timeout", 120.0)),
            supports_vision=vision,
        )

    if kind == "poe":
        return PoeProvider(
            name, cfg.get("model", "GPT-4o"), params,
            api_key=_resolve_secret(cfg, "api_key", "api_key_env") or "",
            base_url=cfg.get("base_url", "https://api.poe.com/v1"),
            timeout=float(cfg.get("timeout", 120.0)),
            supports_vision=vision,
        )

    if kind == "minimax":
        return MiniMaxProvider(
            name, cfg.get("model", "MiniMax-Text-01"), params,
            api_key=_resolve_secret(cfg, "api_key", "api_key_env") or "",
            base_url=cfg.get("base_url", "https://api.minimaxi.com/v1"),
            group_id=_resolve_secret(cfg, "group_id", "group_id_env"),
            timeout=float(cfg.get("timeout", 120.0)),
            supports_vision=vision,
        )

    raise ProviderError(
        f"Unknown provider type {kind!r} for {name!r}. "
        f"Known types: {', '.join(KNOWN_KINDS)}"
    )
