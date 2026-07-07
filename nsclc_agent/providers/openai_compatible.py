"""Base class for OpenAI-compatible chat/completions backends.

Poe and MiniMax both expose OpenAI-shaped chat endpoints, and Azure OpenAI is
OpenAI-shaped with the deployment carried in the URL. This base implements the
shared HTTP request/parse logic on the Python standard library so the common
backends need no third-party dependency. Subclasses customize the endpoint
URL, auth headers and (rarely) the request payload.
"""

from __future__ import annotations

import json
import ssl
import os
import urllib.error
import urllib.request
from typing import Any, Optional

from .base import (
    GenerationParams,
    LLMProvider,
    LLMResponse,
    Message,
    ProviderError,
)


def _ssl_context() -> ssl.SSLContext:
    """Build an SSL context honoring SSL_CERT_FILE / REQUESTS_CA_BUNDLE.

    This lets the client work behind corporate / agent proxies that present a
    custom CA bundle without disabling verification.
    """
    ctx = ssl.create_default_context()
    ca = os.environ.get("SSL_CERT_FILE") or os.environ.get("REQUESTS_CA_BUNDLE")
    if ca and os.path.isfile(ca):
        try:
            ctx.load_verify_locations(ca)
        except ssl.SSLError:  # pragma: no cover - defensive
            pass
    return ctx


class OpenAICompatibleProvider(LLMProvider):
    kind = "openai_compatible"

    def __init__(
        self,
        name: str,
        model: str,
        params: GenerationParams,
        *,
        api_key: str,
        base_url: str,
        chat_path: str = "/chat/completions",
        timeout: float = 120.0,
        auth_scheme: str = "bearer",  # "bearer" | "api-key" | "none"
        extra_headers: Optional[dict[str, str]] = None,
        send_model_in_body: bool = True,
        supports_vision: bool = False,
    ):
        super().__init__(name, model, params)
        if not api_key and auth_scheme != "none":
            raise ProviderError(
                f"Provider {name!r} has no API key. Set the configured "
                f"api_key_env environment variable."
            )
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.chat_path = chat_path
        self.timeout = timeout
        self.auth_scheme = auth_scheme
        self.extra_headers = extra_headers or {}
        self.send_model_in_body = send_model_in_body
        self.supports_vision = supports_vision
        self._ctx = _ssl_context()

    # -- hooks subclasses may override --------------------------------------

    def _endpoint(self) -> str:
        return f"{self.base_url}{self.chat_path}"

    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self.auth_scheme == "bearer":
            headers["Authorization"] = f"Bearer {self.api_key}"
        elif self.auth_scheme == "api-key":
            headers["api-key"] = self.api_key
        headers.update(self.extra_headers)
        return headers

    def _payload(self, messages: list[Message], p: GenerationParams) -> dict:
        body: dict[str, Any] = {
            "messages": [m.to_openai() for m in messages],
            "temperature": p.temperature,
            "max_tokens": p.max_tokens,
        }
        if self.send_model_in_body:
            body["model"] = self.model
        if p.top_p is not None:
            body["top_p"] = p.top_p
        body.update(p.extra)
        return body

    def _parse(self, data: dict) -> LLMResponse:
        try:
            choice = data["choices"][0]
            content = choice["message"]["content"]
            finish = choice.get("finish_reason")
        except (KeyError, IndexError, TypeError) as exc:
            raise ProviderError(
                f"Unexpected response shape from {self.name}: "
                f"{json.dumps(data)[:500]}"
            ) from exc
        if isinstance(content, list):  # some APIs return content parts
            content = "".join(
                part.get("text", "") for part in content
                if isinstance(part, dict)
            )
        return LLMResponse(
            content=content or "",
            provider=self.name,
            model=data.get("model", self.model),
            usage=data.get("usage", {}) or {},
            finish_reason=finish,
            raw=data,
        )

    # -- transport ----------------------------------------------------------

    def complete(
        self, messages: list[Message], *, params: Optional[GenerationParams] = None
    ) -> LLMResponse:
        p = params or self.params
        payload = json.dumps(self._payload(messages, p)).encode("utf-8")
        req = urllib.request.Request(
            self._endpoint(), data=payload, headers=self._headers(),
            method="POST",
        )
        try:
            with urllib.request.urlopen(
                req, timeout=self.timeout, context=self._ctx
            ) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            raise ProviderError(
                f"{self.name}: HTTP {exc.code} from {self._endpoint()}: "
                f"{body[:800]}"
            ) from exc
        except urllib.error.URLError as exc:
            raise ProviderError(
                f"{self.name}: connection error to {self._endpoint()}: "
                f"{exc.reason}"
            ) from exc
        except json.JSONDecodeError as exc:
            raise ProviderError(
                f"{self.name}: non-JSON response from {self._endpoint()}"
            ) from exc
        return self._parse(data)
