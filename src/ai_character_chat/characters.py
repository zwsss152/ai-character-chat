"""Read character cards supplied with the installed package."""

import json
from importlib.resources import files

from pydantic import BaseModel, ConfigDict, Field


class Character(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(pattern=r"^[a-z][a-z0-9_-]*$", max_length=64)
    name: str = Field(min_length=1)
    persona: str = Field(min_length=1)
    greeting: str = Field(min_length=1)
    schema_version: int = Field(default=1, ge=1, le=1)
    identity_facts: list[str] = Field(default_factory=list)
    values: list[str] = Field(default_factory=list)
    speaking_style: str = ""
    knowledge_boundary: list[str] = Field(default_factory=list)
    dialogue_examples: list["DialogueExample"] = Field(default_factory=list)


class DialogueExample(BaseModel):
    model_config = ConfigDict(extra="forbid")

    user: str = Field(min_length=1)
    reply: str = Field(min_length=1)


Character.model_rebuild()


def load_characters() -> dict[str, Character]:
    """Fail clearly on invalid or duplicate cards instead of hiding mistakes."""
    directory = files("ai_character_chat").joinpath("data", "characters")
    characters: dict[str, Character] = {}
    for card in sorted(directory.iterdir(), key=lambda item: item.name):
        if not card.name.endswith(".json"):
            continue
        character = Character.model_validate(json.loads(card.read_text(encoding="utf-8")))
        if character.id in characters:
            raise ValueError(f"Duplicate character id: {character.id}")
        characters[character.id] = character
    if not characters:
        raise ValueError("No character cards found")
    return characters
