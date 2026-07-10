"""Provider-agnostic chat interface.

All backends (LiteLLM, Azure OpenAI, Poe, MiniMax, mock) implement the same
tiny surface: take a list of messages, return an LLMResponse. This is what
lets the agent swap teaching/testing backends without touching clinical logic.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Optional


class ProviderError(RuntimeError):
    """Raised for any provider-side failure (config, transport, API error)."""


@dataclass
class Message:
    role: str  # "system" | "user" | "assistant"
    content: str
    #: optional image references (``data:`` or ``http(s):`` URLs) attached to a
    #: user turn, for vision-capable models. Empty for text-only messages.
    images: list[str] = field(default_factory=list)

    def to_openai(self) -> dict:
        """Render as an OpenAI chat message.

        Text-only messages use the plain-string ``content`` form. When images
        are attached, content becomes the multimodal *parts* array that
        OpenAI-compatible vision endpoints (incl. Poe → Gemini) expect.
        """
        if not self.images:
            return {"role": self.role, "content": self.content}
        parts: list[dict] = []
        if self.content:
            parts.append({"type": "text", "text": self.content})
        for url in self.images:
            parts.append({"type": "image_url", "image_url": {"url": url}})
        return {"role": self.role, "content": parts}


@dataclass
class LLMResponse:
    content: str
    provider: str
    model: str
    usage: dict[str, Any] = field(default_factory=dict)
    finish_reason: Optional[str] = None
    raw: Optional[dict] = None

    def to_dict(self) -> dict:
        return {
            "content": self.content,
            "provider": self.provider,
            "model": self.model,
            "usage": self.usage,
            "finish_reason": self.finish_reason,
        }


@dataclass
class GenerationParams:
    temperature: float = 0.2
    max_tokens: int = 4096
    top_p: Optional[float] = None
    extra: dict[str, Any] = field(default_factory=dict)

    def merged(self, **overrides) -> "GenerationParams":
        data = {
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
            "top_p": self.top_p,
            "extra": dict(self.extra),
        }
        for k, v in overrides.items():
            if v is not None:
                data[k] = v
        return GenerationParams(**data)


class LLMProvider(ABC):
    """Abstract chat provider."""

    #: short backend type identifier, e.g. "azure"
    kind: str = "base"

    def __init__(self, name: str, model: str, params: GenerationParams):
        self.name = name
        self.model = model
        self.params = params
        #: whether this backend/model can accept image inputs. Advisory: it
        #: gates default vision-provider selection and is reported in
        #: ``describe()``; it does not hard-block a call.
        self.supports_vision: bool = False

    @abstractmethod
    def complete(
        self, messages: list[Message], *, params: Optional[GenerationParams] = None
    ) -> LLMResponse:
        """Run a single chat completion."""

    def describe(self) -> dict:
        return {
            "name": self.name,
            "kind": self.kind,
            "model": self.model,
            "supports_vision": self.supports_vision,
        }
