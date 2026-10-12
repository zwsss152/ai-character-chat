"""Validate a single message and coordinate a reply."""

from typing import Annotated

from pydantic import BaseModel, ConfigDict, StringConstraints

from .characters import Character
from .providers import ChatProvider

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
    def __init__(self, characters: dict[str, Character], provider: ChatProvider):
        self.characters = characters
        self.provider = provider

    def reply(self, request: ChatRequest) -> ChatResponse:
        character = self.characters.get(request.character_id)
        if character is None:
            raise UnknownCharacterError(request.character_id)
        # This scaffold is single-turn: it does not save chat history or memory.
        reply = self.provider.generate(character, request.message)
        return ChatResponse(
            character_id=character.id,
            reply=reply,
            mode=self.provider.mode,
        )
