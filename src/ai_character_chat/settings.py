"""Local configuration; never serialize or display the API key."""

import os
from typing import Literal, Self
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .providers import ChatProvider, HTTPProvider, MockProvider


class Settings(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)

    provider: Literal["mock", "http"] = "mock"
    base_url: str | None = None
    model: str | None = None
    api_key: str | None = Field(default=None, repr=False, exclude=True)
    database_path: str = "local_data/chat.sqlite3"
    max_history_turns: int = Field(default=10, ge=0)
    max_context_characters: int = Field(default=16000, gt=0)
    request_timeout_seconds: float = Field(default=20, gt=0)

    @field_validator("model", "api_key")
    @classmethod
    def trim_model_credentials(cls, value: str | None) -> str | None:
        return value.strip() if value is not None else None

    @field_validator("api_key")
    @classmethod
    def valid_api_key(cls, value: str | None) -> str | None:
        if value is not None and (not value.isascii() or "\r" in value or "\n" in value):
            raise ValueError("api_key must be a single-line ASCII credential")
        return value

    @field_validator("base_url")
    @classmethod
    def valid_base_url(cls, value: str | None) -> str | None:
        if value is None:
            return None
        parts = urlsplit(value)
        if (
            parts.scheme not in ("http", "https") or not parts.hostname
            or parts.username or parts.password or parts.query or parts.fragment
        ):
            raise ValueError("base_url must be an HTTP(S) API base URL without embedded credentials")
        return value.rstrip("/")

    @model_validator(mode="after")
    def require_http_config(self) -> Self:
        if self.provider == "http":
            required = ("base_url", "model", "api_key")
            missing = [name for name in required if not (getattr(self, name) or "").strip()]
            if missing:
                raise ValueError("HTTP provider requires: " + ", ".join(missing))
        return self

    @classmethod
    def from_env(cls) -> Self:
        values = {}
        for field in cls.model_fields:
            value = os.environ.get("AI_CHAT_" + field.upper())
            if value is not None:
                values[field] = value
        return cls.model_validate(values)


def make_provider(settings: Settings) -> ChatProvider:
    if settings.provider == "mock":
        return MockProvider()
    return HTTPProvider(
        base_url=settings.base_url,
        model=settings.model,
        api_key=settings.api_key,
        timeout_seconds=settings.request_timeout_seconds,
    )
