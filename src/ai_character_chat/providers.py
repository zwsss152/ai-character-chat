"""The replaceable boundary between application code and a future model."""

from typing import Protocol

from .characters import Character


class ChatProvider(Protocol):
    mode: str

    def generate(self, character: Character, message: str) -> str:
        """Return a reply; a real provider can implement this later."""
        ...


class MockProvider:
    """Deterministic echo only: no model, persona generation, or network calls."""

    mode = "mock"

    def generate(self, character: Character, message: str) -> str:
        return f"[离线模拟｜{character.name}] 收到：{message}"
