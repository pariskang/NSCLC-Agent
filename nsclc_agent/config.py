"""Configuration loading for the NSCLC agent.

Config is a small mapping of named provider blocks plus global generation
defaults. YAML and JSON are both supported; YAML needs PyYAML installed, JSON
works with the standard library alone. When no config file is supplied a
built-in mock-only configuration is used so the toolkit runs immediately.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from .providers.base import GenerationParams

DEFAULT_CONFIG: dict[str, Any] = {
    "default_provider": "mock",
    "generation": {"temperature": 0.2, "max_tokens": 4096},
    "providers": {
        "mock": {"type": "mock", "model": "mock-echo"},
    },
}


class ConfigError(ValueError):
    pass


@dataclass
class Config:
    default_provider: str
    providers: dict[str, dict[str, Any]]
    generation: GenerationParams
    #: provider used to read radiology films (perception layer). Falls back to
    #: any provider flagged ``vision: true``, else the default provider.
    vision_provider: Optional[str] = None
    source: Optional[str] = None
    raw: dict[str, Any] = field(default_factory=dict)

    def provider_names(self) -> list[str]:
        return list(self.providers.keys())

    def provider_config(self, name: str) -> dict[str, Any]:
        if name not in self.providers:
            raise ConfigError(
                f"Provider {name!r} not found. Configured providers: "
                f"{', '.join(self.providers) or '(none)'}"
            )
        return self.providers[name]


def _load_raw(path: Path) -> dict[str, Any]:
    text = path.read_text(encoding="utf-8")
    if path.suffix.lower() in (".yaml", ".yml"):
        try:
            import yaml  # type: ignore
        except ImportError as exc:
            raise ConfigError(
                f"Reading {path.name} needs PyYAML (`pip install pyyaml`), or "
                f"convert the config to JSON."
            ) from exc
        data = yaml.safe_load(text)
    elif path.suffix.lower() == ".json":
        data = json.loads(text)
    else:
        # Try YAML first (superset), fall back to JSON.
        try:
            import yaml  # type: ignore
            data = yaml.safe_load(text)
        except ImportError:
            data = json.loads(text)
    if not isinstance(data, dict):
        raise ConfigError(f"Config root must be a mapping, got {type(data)}")
    return data


def load_config(path: Optional[str | os.PathLike] = None) -> Config:
    """Load configuration from a file, or the built-in mock-only default."""
    if path is None:
        env_path = os.environ.get("NSCLC_AGENT_CONFIG")
        if env_path:
            path = env_path
    if path is None:
        raw = dict(DEFAULT_CONFIG)
        source = "(built-in default)"
    else:
        p = Path(path)
        if not p.is_file():
            raise ConfigError(f"Config file not found: {p}")
        raw = _load_raw(p)
        source = str(p)

    providers = raw.get("providers") or {}
    if not isinstance(providers, dict) or not providers:
        raise ConfigError("Config must define at least one provider under "
                          "'providers'")
    gen = raw.get("generation") or {}
    generation = GenerationParams(
        temperature=float(gen.get("temperature", 0.2)),
        max_tokens=int(gen.get("max_tokens", 4096)),
        top_p=gen.get("top_p"),
        extra=gen.get("extra", {}) or {},
    )
    default_provider = raw.get("default_provider") or next(iter(providers))
    if default_provider not in providers:
        raise ConfigError(
            f"default_provider {default_provider!r} is not among configured "
            f"providers: {', '.join(providers)}"
        )
    vision_provider = raw.get("vision_provider")
    if vision_provider is None:
        # auto-pick the first provider explicitly flagged vision-capable
        vision_provider = next(
            (n for n, p in providers.items()
             if isinstance(p, dict) and p.get("vision")),
            None,
        )
    if vision_provider is not None and vision_provider not in providers:
        raise ConfigError(
            f"vision_provider {vision_provider!r} is not among configured "
            f"providers: {', '.join(providers)}"
        )
    return Config(
        default_provider=default_provider,
        providers=providers,
        generation=generation,
        vision_provider=vision_provider,
        source=source,
        raw=raw,
    )
