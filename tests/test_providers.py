import json

import httpx
import pytest
from pydantic import ValidationError

from ai_character_chat.characters import Character
from ai_character_chat.context import build_context
from ai_character_chat.providers import GenerationRequest, HTTPProvider, MockProvider, ProviderError
from ai_character_chat.settings import Settings, make_provider


@pytest.fixture
def generation_request():
    character = Character(id="test", name="露米", persona="档案员", greeting="你好")
    context = build_context(character, [], "当前消息")
    return GenerationRequest(character=character, messages=context.messages)


def provider(handler):
    return HTTPProvider(
        "https://model.example/v1/", "configured-model", "test-secret",
        transport=httpx.MockTransport(handler),
    )


def test_mock_echo_is_labeled_and_uses_latest_user_message(generation_request):
    result = MockProvider().generate(generation_request)
    assert result.reply == "[离线模拟｜露米] 收到：当前消息"
    assert result.mode == "mock" and result.model == "mock-echo"
    assert result.usage is None and result.elapsed_ms >= 0


def test_http_sends_ordered_context_and_parses_reply_usage_and_model(generation_request):
    calls = []

    def handler(http_request):
        calls.append(http_request)
        assert http_request.method == "POST"
        assert str(http_request.url) == "https://model.example/v1/chat/completions"
        assert http_request.headers["authorization"] == "Bearer test-secret"
        assert http_request.extensions["timeout"] == {"connect": 20, "read": 20, "write": 20, "pool": 20}
        body = json.loads(http_request.content)
        assert body == {
            "model": "configured-model", "stream": False,
            "messages": [item.model_dump() for item in generation_request.messages],
        }
        return httpx.Response(200, json={
            "model": "actual-model", "choices": [{"message": {"content": "一起核对吧。"}}],
            "usage": {"prompt_tokens": 123, "completion_tokens": 7, "total_tokens": 130,
                      "completion_tokens_details": {"reasoning_tokens": 0}},
        })

    client = provider(handler)
    try:
        result = client.generate(generation_request)
        assert result.reply == "一起核对吧。" and result.model == "actual-model"
        assert result.mode == "http" and result.elapsed_ms >= 0
        assert result.usage == {"prompt_tokens": 123, "completion_tokens": 7, "total_tokens": 130}
        assert len(calls) == 1
        assert "test-secret" not in repr(client)
    finally:
        client.close()


def test_http_accepts_optional_usage_and_model_metadata(generation_request):
    client = provider(lambda _: httpx.Response(200, json={"choices": [{"message": {"content": "答复"}}]}))
    try:
        result = client.generate(generation_request)
        assert result.model == "configured-model" and result.usage is None
    finally:
        client.close()


@pytest.mark.parametrize("status,code,http_status", [
    (401, "authentication_error", 502), (403, "authentication_error", 502),
    (429, "rate_limited", 503), (500, "upstream_error", 502),
    (400, "upstream_error", 502), (302, "upstream_error", 502),
])
def test_upstream_errors_are_classified_without_raw_response_or_retry(generation_request, status, code, http_status):
    calls = []

    def handler(http_request):
        calls.append(http_request)
        return httpx.Response(status, text="test-secret upstream raw details", headers={"location": "https://other.example"})

    client = provider(handler)
    try:
        with pytest.raises(ProviderError) as captured:
            client.generate(generation_request)
        error = captured.value
        assert error.code == code and error.http_status == http_status
        assert "test-secret" not in str(error) and "raw details" not in str(error)
        assert len(calls) == 1
    finally:
        client.close()


@pytest.mark.parametrize("failure,code,status", [
    (httpx.ReadTimeout, "timeout", 504), (httpx.ConnectError, "connection_error", 502),
])
def test_transport_errors_have_safe_messages_and_are_not_retried(generation_request, failure, code, status):
    calls = []

    def handler(http_request):
        calls.append(http_request)
        raise failure("test-secret connection detail", request=http_request)

    client = provider(handler)
    try:
        with pytest.raises(ProviderError) as captured:
            client.generate(generation_request)
        assert captured.value.code == code and captured.value.http_status == status
        assert "test-secret" not in str(captured.value)
        assert captured.value.__suppress_context__
        assert len(calls) == 1
    finally:
        client.close()


@pytest.mark.parametrize("body", [
    {}, [], {"choices": []}, {"choices": [{"message": {"content": None}}]},
    {"choices": [{"message": {"content": "  "}}]},
    {"choices": [{"message": {"content": ["unsupported"]}}]},
    {"choices": [{"message": {"content": "ok"}}], "usage": "bad"},
    {"choices": [{"message": {"content": "ok"}}], "model": None},
])
def test_invalid_success_payload_has_safe_error(generation_request, body):
    client = provider(lambda _: httpx.Response(200, json=body))
    try:
        with pytest.raises(ProviderError, match="无法解析") as captured:
            client.generate(generation_request)
        assert captured.value.code == "invalid_response"
    finally:
        client.close()


def test_invalid_json_is_reported_without_echoing_body(generation_request):
    client = provider(lambda _: httpx.Response(200, text="test-secret invalid JSON"))
    try:
        with pytest.raises(ProviderError) as captured:
            client.generate(generation_request)
        assert captured.value.code == "invalid_response"
        assert "test-secret" not in str(captured.value)
    finally:
        client.close()


def test_settings_default_mock_and_http_requires_complete_configuration():
    assert isinstance(make_provider(Settings()), MockProvider)
    with pytest.raises(ValidationError, match="HTTP provider requires"):
        Settings(provider="http")
    settings = Settings(provider="http", base_url="https://model.example/v1", model="m", api_key="test-secret")
    assert "test-secret" not in repr(settings)
    assert "api_key" not in settings.model_dump()
    assert "test-secret" not in settings.model_dump_json()


def test_environment_settings_are_loaded_with_explicit_prefix(monkeypatch):
    for field in Settings.model_fields:
        monkeypatch.delenv("AI_CHAT_" + field.upper(), raising=False)
    monkeypatch.setenv("AI_CHAT_PROVIDER", "http")
    monkeypatch.setenv("AI_CHAT_BASE_URL", "https://model.example/v1")
    monkeypatch.setenv("AI_CHAT_MODEL", "test-model")
    monkeypatch.setenv("AI_CHAT_API_KEY", "test-secret")
    monkeypatch.setenv("AI_CHAT_MAX_HISTORY_TURNS", "4")
    monkeypatch.setenv("AI_CHAT_MAX_CONTEXT_CHARACTERS", "9000")
    monkeypatch.setenv("AI_CHAT_REQUEST_TIMEOUT_SECONDS", "3.5")
    settings = Settings.from_env()
    assert settings.provider == "http" and settings.model == "test-model"
    assert settings.api_key == "test-secret"
    assert settings.max_history_turns == 4 and settings.max_context_characters == 9000
    assert settings.request_timeout_seconds == 3.5


def test_invalid_credentials_fail_safely_at_configuration_time():
    with pytest.raises(ValidationError) as captured:
        Settings(api_key="secret\ninjected-header")
    assert "secret" not in str(captured.value)
    assert Settings(model=" model ", api_key=" key ").api_key == "key"


@pytest.mark.parametrize("base_url", ["ftp://model.example", "https://user:secret@model.example", "https://model.example?key=secret"])
def test_unsafe_or_invalid_base_urls_are_rejected(base_url):
    with pytest.raises(ValidationError) as captured:
        Settings(base_url=base_url)
    assert base_url not in str(captured.value)
