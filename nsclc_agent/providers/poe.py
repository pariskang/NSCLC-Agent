"""Poe provider.

Poe exposes an OpenAI-compatible endpoint at https://api.poe.com/v1 with
Bearer authentication. Model names are Poe bot names, e.g. "GPT-4o",
"Claude-Sonnet-4", "Gemini-2.5-Pro".
"""

from __future__ import annotations

from .base import GenerationParams
from .openai_compatible import OpenAICompatibleProvider

DEFAULT_BASE_URL = "https://api.poe.com/v1"


class PoeProvider(OpenAICompatibleProvider):
    kind = "poe"

    def __init__(
        self,
        name: str,
        model: str,
        params: GenerationParams,
        *,
        api_key: str,
        base_url: str = DEFAULT_BASE_URL,
        timeout: float = 120.0,
        supports_vision: bool = False,
    ):
        super().__init__(
            name, model, params,
            api_key=api_key,
            base_url=base_url,
            chat_path="/chat/completions",
            timeout=timeout,
            auth_scheme="bearer",
            supports_vision=supports_vision,
        )
