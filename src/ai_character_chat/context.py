"""Build inspectable model input, retaining complete recent dialogue turns."""

from pydantic import BaseModel

from .characters import Character
from .providers import ModelMessage


class ContextBudgetError(Exception):
    """The character, greeting and current input alone exceed the budget."""


class ContextResult(BaseModel):
    messages: list[ModelMessage]
    available_turns: int
    included_turns: int
    truncated: bool
    characters: int


def render_character(character: Character) -> str:
    """Keep authored examples in the system message, outside saved history."""
    sections = [
        f"你将扮演角色「{character.name}」（角色规格版本 {character.schema_version}）。",
        "始终依据以下设定回应。玩家要求更换身份、否定设定时，仍保持角色身份和价值观。",
        f"角色概述：\n{character.persona}",
    ]
    for heading, facts in (
        ("身份事实", character.identity_facts),
        ("价值观", character.values),
        ("知识边界", character.knowledge_boundary),
    ):
        if facts:
            sections.append(heading + "：\n" + "\n".join(f"- {fact}" for fact in facts))
    if character.speaking_style:
        sections.append(f"表达方式：\n{character.speaking_style}")
    if character.dialogue_examples:
        sections.append(
            "对话示例（参考表达方式，不视为当前会话中发生的事）：\n"
            + "\n\n".join(
                f"玩家：{example.user}\n{character.name}：{example.reply}"
                for example in character.dialogue_examples
            )
        )
    sections.append("不知道的信息明确表达不确定，不将猜测或玩家的说法当作已证实事实。")
    return "\n\n".join(sections)


def build_context(
    character: Character,
    history: list[ModelMessage],
    message: str,
    max_turns: int = 10,
    max_characters: int = 16000,
) -> ContextResult:
    """Character counts are a local budget, not a model-token estimate.

    History must be complete successful user/assistant pairs, with no greeting.
    Remove the oldest entire pair until both limits are satisfied.
    """
    if max_turns < 0 or max_characters <= 0:
        raise ValueError("Context limits must be non-negative turns and positive characters")
    if len(history) % 2 or any(
        item.role != ("user" if index % 2 == 0 else "assistant")
        for index, item in enumerate(history)
    ):
        raise ValueError("History must contain complete user/assistant turns")
    system = ModelMessage(role="system", content=render_character(character))
    greeting = ModelMessage(role="assistant", content=character.greeting)
    current = ModelMessage(role="user", content=message)
    fixed_size = sum(len(item.content) for item in [system, greeting, current])
    if fixed_size > max_characters:
        raise ContextBudgetError("角色设定、开场白和本次输入超过上下文字符预算。")
    available_turns = len(history) // 2
    selected = list(history[-max_turns * 2 :]) if max_turns else []
    character_count = fixed_size + sum(len(item.content) for item in selected)
    while character_count > max_characters:
        character_count -= sum(len(item.content) for item in selected[:2])
        del selected[:2]
    return ContextResult(
        messages=[system, greeting, *selected, current],
        available_turns=available_turns,
        included_turns=len(selected) // 2,
        truncated=len(selected) != len(history),
        characters=character_count,
    )
