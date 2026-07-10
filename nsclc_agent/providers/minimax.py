"""MiniMax provider.

MiniMax's chat endpoint (``/v1/text/chatcompletion_v2``) is OpenAI-shaped:
Bearer auth, ``messages`` array, ``choices[0].message.content`` response.
Endpoints differ by region:
  * Mainland China: https://api.minimaxi.com
  * International:   https://api.minimax.io
Both are overridable via ``base_url``. Some tenants also require a GroupId
query parameter, supported here via ``group_id``.
"""

from __future__ import annotations

from typing import Optional

from .base import GenerationParams
from .openai_compatible import OpenAICompatibleProvider

DEFAULT_BASE_URL = "https://api.minimaxi.com/v1"
CHAT_PATH = "/text/chatcompletion_v2"


class MiniMaxProvider(OpenAICompatibleProvider):
    kind = "minimax"

    def __init__(
        self,
        name: str,
        model: str,
        params: GenerationParams,
        *,
        api_key: str,
        base_url: str = DEFAULT_BASE_URL,
        group_id: Optional[str] = None,
        timeout: float = 120.0,
        supports_vision: bool = False,
    ):
        chat_path = CHAT_PATH
        if group_id:
            chat_path = f"{CHAT_PATH}?GroupId={group_id}"
        super().__init__(
            name, model, params,
            api_key=api_key,
            base_url=base_url,
            chat_path=chat_path,
            timeout=timeout,
            auth_scheme="bearer",
            supports_vision=supports_vision,
        )
        self.group_id = group_id
