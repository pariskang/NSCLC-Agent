"""LiteLLM provider.

LiteLLM is a unified SDK that routes a single ``completion`` call to 100+
backends (OpenAI, Azure, Anthropic, Bedrock, Vertex, local models, ...). Using
it here means a case can be run against anything LiteLLM supports by changing
only the ``model`` string, e.g. "gpt-4o", "azure/my-deployment",
"anthropic/claude-sonnet-4", "openai/gpt-4o" via a proxy.

The ``litellm`` package is an optional dependency; it is imported lazily so the
rest of the toolkit works without it installed.
"""

from __future__ import annotations

from typing import Any, Optional

from .base import (
    GenerationParams,
    LLMProvider,
    LLMResponse,
    Message,
    ProviderError,
)


class LiteLLMProvider(LLMProvider):
    kind = "litellm"

    def __init__(
        self,
        name: str,
        model: str,
        params: GenerationParams,
        *,
        api_key: Optional[str] = None,
        api_base: Optional[str] = None,
        extra_kwargs: Optional[dict[str, Any]] = None,
        supports_vision: bool = False,
    ):
        super().__init__(name, model, params)
        self.api_key = api_key
        self.api_base = api_base
        self.extra_kwargs = extra_kwargs or {}
        self.supports_vision = supports_vision

    def _litellm(self):
        try:
            import litellm  # type: ignore
        except ImportError as exc:  # pragma: no cover - depends on env
            raise ProviderError(
                "The 'litellm' package is not installed. Install it with "
                "`pip install litellm` to use the litellm provider."
            ) from exc
        return litellm

    def complete(
        self, messages: list[Message], *, params: Optional[GenerationParams] = None
    ) -> LLMResponse:
        litellm = self._litellm()
        p = params or self.params
        kwargs: dict[str, Any] = {
            "model": self.model,
            "messages": [m.to_openai() for m in messages],
            "temperature": p.temperature,
            "max_tokens": p.max_tokens,
        }
        if p.top_p is not None:
            kwargs["top_p"] = p.top_p
        if self.api_key:
            kwargs["api_key"] = self.api_key
        if self.api_base:
            kwargs["api_base"] = self.api_base
        kwargs.update(self.extra_kwargs)
        kwargs.update(p.extra)
        try:
            resp = litellm.completion(**kwargs)
        except Exception as exc:  # litellm raises many provider-specific types
            raise ProviderError(f"{self.name}: litellm error: {exc}") from exc
        return self._parse(resp)

    def _parse(self, resp: Any) -> LLMResponse:
        # litellm returns an OpenAI-style object; support dict or attr access.
        def get(obj, key, default=None):
            if isinstance(obj, dict):
                return obj.get(key, default)
            return getattr(obj, key, default)

        try:
            choice = get(resp, "choices")[0]
            message = get(choice, "message")
            content = get(message, "content") or ""
            finish = get(choice, "finish_reason")
        except (TypeError, IndexError, AttributeError) as exc:
            raise ProviderError(
                f"{self.name}: unexpected litellm response shape"
            ) from exc
        usage = get(resp, "usage", {}) or {}
        if not isinstance(usage, dict):
            usage = {
                "prompt_tokens": get(usage, "prompt_tokens"),
                "completion_tokens": get(usage, "completion_tokens"),
                "total_tokens": get(usage, "total_tokens"),
            }
        return LLMResponse(
            content=content,
            provider=self.name,
            model=get(resp, "model", self.model) or self.model,
            usage=usage,
            finish_reason=finish,
        )
