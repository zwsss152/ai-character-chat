"""Model boundary: deterministic mock or a compatible HTTP chat API."""

from time import perf_counter
from typing import Literal, Protocol

import httpx
from pydantic import BaseModel, ConfigDict, Field

from .characters import Character


class ModelMessage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    role: Literal["system", "user", "assistant"]
    content: str


class GenerationRequest(BaseModel):
    character: Character
    messages: list[ModelMessage]


class GenerationResult(BaseModel):
    reply: str = Field(min_length=1)
    mode: str
    model: str
    usage: dict[str, int] | None = None
    elapsed_ms: float = Field(default=0, ge=0)


class ProviderError(Exception):
    """Only safe application-owned text should cross the HTTP boundary."""

    def __init__(self, code: str, message: str, http_status: int = 502):
        super().__init__(message)
        self.code = code
        self.message = message
        self.http_status = http_status


class ChatProvider(Protocol):
    mode: str

    def generate(self, request: GenerationRequest) -> GenerationResult: ...


class MockProvider:
    """Input echo only: role prompts are inspectable but not executed."""

    mode = "mock"

    def generate(self, request: GenerationRequest) -> GenerationResult:
        started = perf_counter()
        message = next(item.content for item in reversed(request.messages) if item.role == "user")
        return GenerationResult(
            reply=f"[离线模拟｜{request.character.name}] 收到：{message}",
            mode=self.mode,
            model="mock-echo",
            elapsed_ms=(perf_counter() - started) * 1000,
        )


class HTTPProvider:
    """Single attempt against base_url/chat/completions; never auto-retry."""

    mode = "http"

    def __init__(
        self,
        base_url: str,
        model: str,
        api_key: str,
        timeout_seconds: float = 20,
        *,
        transport: httpx.BaseTransport | None = None,
    ):
        self.model = model
        self._url = base_url.rstrip("/") + "/chat/completions"
        self._client = httpx.Client(
            headers={"Authorization": "Bearer " + api_key},
            timeout=timeout_seconds,
            transport=transport,
            follow_redirects=False,
        )

    def close(self) -> None:
        self._client.close()

    def generate(self, request: GenerationRequest) -> GenerationResult:
        started = perf_counter()
        try:
            response = self._client.post(
                self._url,
                json={
                    "model": self.model,
                    "messages": [item.model_dump() for item in request.messages],
                    "stream": False,
                },
            )
        except httpx.TimeoutException:
            raise ProviderError("timeout", "模型服务响应超时，请稍后重试。", 504) from None
        except httpx.RequestError:
            raise ProviderError("connection_error", "无法连接模型服务，请检查本地配置或网络。") from None
        if response.status_code in (401, 403):
            raise ProviderError("authentication_error", "模型服务认证失败，请检查本地密钥。")
        if response.status_code == 429:
            raise ProviderError("rate_limited", "模型服务请求过于频繁，请稍后重试。", 503)
        if response.status_code >= 500:
            raise ProviderError("upstream_error", "模型服务暂时不可用，请稍后重试。")
        if not 200 <= response.status_code < 300:
            raise ProviderError("upstream_error", "模型服务拒绝了本次请求，请检查本地配置。")
        try:
            payload = response.json()
            choices = payload["choices"]
            reply = choices[0]["message"]["content"]
            if not isinstance(choices, list) or not isinstance(reply, str) or not reply.strip():
                raise ValueError("Missing text response")
            usage = payload.get("usage")
            if usage is not None:
                if not isinstance(usage, dict):
                    raise ValueError("Invalid usage")
                # Compatible services may include nested token details. Keep only counts.
                usage = {
                    key: value for key, value in usage.items()
                    if isinstance(value, int) and not isinstance(value, bool) and value >= 0
                }
            result_model = payload.get("model", self.model)
            if not isinstance(result_model, str) or not result_model.strip():
                raise ValueError("Invalid model identifier")
        except (ValueError, TypeError, KeyError, IndexError, AttributeError):
            raise ProviderError("invalid_response", "模型服务返回了无法解析的文字回复。") from None
        return GenerationResult(
            reply=reply,
            mode=self.mode,
            model=result_model,
            usage=usage,
            elapsed_ms=(perf_counter() - started) * 1000,
        )
