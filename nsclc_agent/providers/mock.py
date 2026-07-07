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


#: marker the imaging-extraction system prompt carries so the offline mock can
#: recognise a "read the films" request and answer with a findings-shaped stub.
IMAGING_MARKER = "IMAGING DESCRIPTOR EXTRACTION"


class MockProvider(LLMProvider):
    kind = "mock"

    def __init__(
        self,
        name: str = "mock",
        model: str = "mock-echo",
        params: Optional[GenerationParams] = None,
        *,
        supports_vision: bool = True,
    ):
        super().__init__(name, model, params or GenerationParams())
        # The mock can stand in for a vision backend so the imaging pipeline is
        # exercisable offline; default True (override via config `vision: false`).
        self.supports_vision = supports_vision

    def complete(
        self, messages: list[Message], *, params: Optional[GenerationParams] = None
    ) -> LLMResponse:
        system = next((m.content for m in messages if m.role == "system"), "")
        user = next((m.content for m in messages if m.role == "user"), "")
        n_images = sum(len(m.images) for m in messages)

        if IMAGING_MARKER in system:
            return self._mock_imaging_read(n_images)

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

    def _mock_imaging_read(self, n_images: int) -> LLMResponse:
        """Findings-shaped stub for the imaging-extraction path (offline).

        Deliberately returns *incomplete* descriptors (candidate_n unresolved)
        to exercise the downstream "propose, don't stage" and next-step logic
        without pretending the mock can actually read a scan.
        """
        stub = {
            "_mock": True,
            "modality": "unknown",
            "candidate_t": None,
            "candidate_n": None,
            "candidate_m": None,
            "nodal_stations": [],
            "measurable_lesions": [],
            "malignant_effusion": None,
            "metastatic_sites": [],
            "confidence": "none",
            "uncertainties": [
                "Offline mock provider cannot read images; configure a "
                "vision backend (e.g. Poe → Gemini) for real film reading."
            ],
            "images_received": n_images,
            "disclaimer": "Model-proposed, UNVERIFIED radiographic descriptors.",
        }
        return LLMResponse(
            content=json.dumps(stub, ensure_ascii=False, indent=2),
            provider=self.name,
            model=self.model,
            usage={"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
            finish_reason="stop",
        )
