"""Tests for the provider layer and factory."""

import json

import pytest

from nsclc_agent.providers import build_provider, GenerationParams, Message, ProviderError
from nsclc_agent.providers.mock import MockProvider
from nsclc_agent.providers.azure import AzureOpenAIProvider
from nsclc_agent.providers.poe import PoeProvider
from nsclc_agent.providers.minimax import MiniMaxProvider


def test_mock_provider_returns_json():
    prov = MockProvider()
    resp = prov.complete([
        Message("system", "STAGE IIIB MODULE"),
        Message("user", "hello"),
    ])
    data = json.loads(resp.content)
    assert data["_mock"] is True
    assert resp.provider == "mock"


def test_build_mock_from_config():
    prov = build_provider("mock", {"type": "mock"})
    assert prov.kind == "mock"


def test_build_unknown_type_raises():
    with pytest.raises(ProviderError):
        build_provider("x", {"type": "does-not-exist"})


def test_build_missing_type_raises():
    with pytest.raises(ProviderError):
        build_provider("x", {})


def test_azure_url_construction():
    prov = build_provider("azure", {
        "type": "azure",
        "endpoint": "https://r.openai.azure.com/",
        "deployment": "gpt-4o",
        "api_version": "2024-10-21",
        "api_key": "secret",
    })
    assert isinstance(prov, AzureOpenAIProvider)
    url = prov._endpoint()
    assert url == (
        "https://r.openai.azure.com/openai/deployments/gpt-4o/"
        "chat/completions?api-version=2024-10-21"
    )
    headers = prov._headers()
    assert headers["api-key"] == "secret"
    # model must NOT be in the body for Azure
    body = prov._payload([Message("user", "hi")], prov.params)
    assert "model" not in body


def test_poe_defaults_and_bearer():
    prov = build_provider("poe", {
        "type": "poe", "model": "GPT-4o", "api_key": "k",
    })
    assert isinstance(prov, PoeProvider)
    assert prov._endpoint() == "https://api.poe.com/v1/chat/completions"
    assert prov._headers()["Authorization"] == "Bearer k"
    assert prov._payload([Message("user", "hi")], prov.params)["model"] == "GPT-4o"


def test_minimax_group_id_in_url():
    prov = build_provider("minimax", {
        "type": "minimax", "model": "MiniMax-Text-01",
        "api_key": "k", "group_id": "g123",
    })
    assert isinstance(prov, MiniMaxProvider)
    assert "GroupId=g123" in prov._endpoint()
    assert prov._endpoint().endswith("/text/chatcompletion_v2?GroupId=g123")


def test_api_key_from_env(monkeypatch):
    monkeypatch.setenv("MY_POE_KEY", "from-env")
    prov = build_provider("poe", {
        "type": "poe", "model": "GPT-4o", "api_key_env": "MY_POE_KEY",
    })
    assert prov.api_key == "from-env"


def test_missing_key_raises():
    with pytest.raises(ProviderError):
        build_provider("poe", {"type": "poe", "model": "GPT-4o",
                               "api_key_env": "DEFINITELY_UNSET_VAR_XYZ"})


def test_openai_compatible_parse_content_parts():
    prov = MiniMaxProvider("m", "MiniMax-Text-01", GenerationParams(),
                           api_key="k")
    data = {
        "model": "MiniMax-Text-01",
        "choices": [{"message": {"content": [{"text": "a"}, {"text": "b"}]},
                     "finish_reason": "stop"}],
        "usage": {"total_tokens": 3},
    }
    resp = prov._parse(data)
    assert resp.content == "ab"
    assert resp.usage["total_tokens"] == 3
