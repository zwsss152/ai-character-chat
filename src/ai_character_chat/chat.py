"""Validate a single message and coordinate a reply."""

from typing import Annotated

from pydantic import BaseModel, ConfigDict, StringConstraints

from .characters import Character
from .context import ContextBudgetError, build_context
from .providers import ChatProvider, GenerationRequest, ProviderError

CharacterId = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=64)
]
Message = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=2000)
]


class ChatRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    character_id: CharacterId
    message: Message


class ChatResponse(BaseModel):
    character_id: str
    reply: str
    mode: str


class UnknownCharacterError(Exception):
    pass


class ChatService:
    def __init__(
        self,
        characters: dict[str, Character],
        provider: ChatProvider,
        *,
        max_context_characters: int = 16000,
    ):
        self.characters = characters
        self.provider = provider
        self.max_context_characters = max_context_characters

    def reply(self, request: ChatRequest) -> ChatResponse:
        character = self.characters.get(request.character_id)
        if character is None:
            raise UnknownCharacterError(request.character_id)
        try:
            context = build_context(
                character, [], request.message, max_characters=self.max_context_characters,
            )
            result = self.provider.generate(
                GenerationRequest(character=character, messages=context.messages)
            )
            return ChatResponse(character_id=character.id, reply=result.reply, mode=result.mode)
        except ContextBudgetError:
            raise ProviderError(
                "context_budget_exceeded", "角色设定与当前输入超过上下文字符预算。", 422,
            ) from None
        except ProviderError:
            raise
        except Exception:
            raise ProviderError("provider_error", "生成回复失败，请稍后重试。") from None
