"""Azure OpenAI provider.

Azure OpenAI is OpenAI-shaped but carries the *deployment* in the URL and uses
the ``api-key`` header plus an ``api-version`` query parameter. The model is
therefore not sent in the request body.

Endpoint pattern:
  {endpoint}/openai/deployments/{deployment}/chat/completions?api-version={ver}
"""

from __future__ import annotations

from .base import GenerationParams, ProviderError
from .openai_compatible import OpenAICompatibleProvider

DEFAULT_API_VERSION = "2024-10-21"


class AzureOpenAIProvider(OpenAICompatibleProvider):
    kind = "azure"

    def __init__(
        self,
        name: str,
        deployment: str,
        params: GenerationParams,
        *,
        api_key: str,
        endpoint: str,
        api_version: str = DEFAULT_API_VERSION,
        timeout: float = 120.0,
        supports_vision: bool = False,
    ):
        if not endpoint:
            raise ProviderError(
                f"Azure provider {name!r} requires an 'endpoint' "
                f"(e.g. https://my-resource.openai.azure.com)"
            )
        endpoint = endpoint.rstrip("/")
        base_url = f"{endpoint}/openai/deployments/{deployment}"
        chat_path = f"/chat/completions?api-version={api_version}"
        super().__init__(
            name, deployment, params,
            api_key=api_key,
            base_url=base_url,
            chat_path=chat_path,
            timeout=timeout,
            auth_scheme="api-key",
            send_model_in_body=False,
            supports_vision=supports_vision,
        )
        self.endpoint = endpoint
        self.deployment = deployment
        self.api_version = api_version
