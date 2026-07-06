"""Offline mock provider.

Returns a deterministic, well-formed stub response without any network or API
key. This is what makes the toolkit runnable for teaching, CI and unit tests
out of the box: the full case → stage → route → prompt pipeline executes, and
only the final model call is stubbed. The stub echoes the routing/staging
context so the wiring is visible.
"""

from __future__ import annotations

import json
from typing import Optional

from .base import GenerationParams, LLMProvider, LLMResponse, Message


class MockProvider(LLMProvider):
    kind = "mock"

    def __init__(
        self,
        name: str = "mock",
        model: str = "mock-echo",
        params: Optional[GenerationParams] = None,
    ):
        super().__init__(name, model, params or GenerationParams())

    def complete(
        self, messages: list[Message], *, params: Optional[GenerationParams] = None
    ) -> LLMResponse:
        system = next((m.content for m in messages if m.role == "system"), "")
        user = next((m.content for m in messages if m.role == "user"), "")
        module_hint = ""
        for line in system.splitlines():
            if "STAGE" in line and "MODULE" in line.upper():
                module_hint = line.strip()
                break
        stub = {
            "_mock": True,
            "note": (
                "This is a deterministic offline stub from the mock provider. "
                "Configure a real backend (litellm / azure / poe / minimax) to "
                "generate an actual clinical decision-support response."
            ),
            "routed_module_banner": module_hint,
            "system_prompt_chars": len(system),
            "user_message_preview": user[:400],
        }
        content = json.dumps(stub, ensure_ascii=False, indent=2)
        return LLMResponse(
            content=content,
            provider=self.name,
            model=self.model,
            usage={"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
            finish_reason="stop",
        )
