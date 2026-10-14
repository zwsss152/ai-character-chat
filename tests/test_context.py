import pytest
from pydantic import ValidationError

from ai_character_chat.characters import Character, load_characters
from ai_character_chat.chat import ChatRequest, ChatService
from ai_character_chat.context import ContextBudgetError, build_context
from ai_character_chat.providers import MockProvider, ModelMessage, ProviderError


@pytest.fixture
def character():
    return Character(
        id="test", name="档案员", persona="负责核对来源。", greeting="一起核对吧。",
        identity_facts=["我是见习档案员。"], values=["不编造证据。"],
        speaking_style="简洁且温和。", knowledge_boundary=["未整理的资料不声称知道。"],
        dialogue_examples=[{"user": "是真的吗？", "reply": "先核对来源。"}],
    )


def turn(number):
    return [
        ModelMessage(role="user", content=f"问题{number}"),
        ModelMessage(role="assistant", content=f"回答{number}"),
    ]


def test_legacy_character_cards_have_defaults_and_can_be_loaded():
    legacy = Character(id="old", name="旧角色", persona="设定", greeting="你好")
    assert legacy.schema_version == 1
    assert legacy.identity_facts == [] and legacy.dialogue_examples == []
    assert load_characters()["lumi"].identity_facts
    with pytest.raises(ValidationError):
        Character(**legacy.model_dump(), unexpected="field")
    with pytest.raises(ValidationError):
        Character(**{**legacy.model_dump(), "schema_version": 2})
    with pytest.raises(ValidationError):
        Character(**{**legacy.model_dump(), "dialogue_examples": [{"user": "?", "reply": "!", "secret": "x"}]})


def test_context_contains_authored_spec_greeting_history_and_current_input(character):
    history = turn(1) + turn(2)
    result = build_context(character, history, "新的问题")
    system = result.messages[0]
    assert system.role == "system"
    for value in (
        character.name, character.persona, *character.identity_facts, *character.values,
        character.speaking_style, *character.knowledge_boundary,
        character.dialogue_examples[0].user, character.dialogue_examples[0].reply,
    ):
        assert value in system.content
    assert [message.role for message in result.messages] == [
        "system", "assistant", "user", "assistant", "user", "assistant", "user",
    ]
    assert result.messages[1].content == character.greeting
    assert result.messages[2:-1] == history
    assert result.messages[-1].content == "新的问题"
    assert result.available_turns == result.included_turns == 2
    assert not result.truncated
    assert result.characters == sum(len(message.content) for message in result.messages)


def test_turn_limit_removes_oldest_complete_turns_without_changing_history(character):
    history = [message for index in range(12) for message in turn(index)]
    original = list(history)
    result = build_context(character, history, "现在")
    assert result.available_turns == 12 and result.included_turns == 10
    assert result.truncated
    assert result.messages[2:-1] == history[4:]
    assert history == original


def test_character_budget_removes_a_whole_turn_even_if_one_message_would_fit(character):
    fixed = build_context(character, [], "现在").characters
    history = turn(1) + turn(2)
    # Both messages of the latest turn fit; only one message of the previous one would fit.
    latest_size = sum(len(message.content) for message in turn(2))
    result = build_context(character, history, "现在", max_characters=fixed + latest_size + 1)
    assert result.messages[2:-1] == turn(2)
    assert result.included_turns == 1 and result.truncated


def test_zero_turns_and_exact_fixed_budget_still_preserve_greeting_and_current(character):
    fixed = build_context(character, [], "现在").characters
    result = build_context(character, turn(1), "现在", max_turns=0, max_characters=fixed)
    assert [item.role for item in result.messages] == ["system", "assistant", "user"]
    assert result.included_turns == 0 and result.truncated
    with pytest.raises(ContextBudgetError):
        build_context(character, [], "现在", max_characters=fixed - 1)


@pytest.mark.parametrize("history", [
    [ModelMessage(role="user", content="half")],
    [ModelMessage(role="assistant", content="greeting"), ModelMessage(role="user", content="wrong")],
    [ModelMessage(role="user", content="one"), ModelMessage(role="system", content="wrong")],
])
def test_invalid_or_partial_history_is_not_silently_rewritten(character, history):
    with pytest.raises(ValueError, match="complete user/assistant"):
        build_context(character, history, "现在")


@pytest.mark.parametrize("limits", [{"max_turns": -1}, {"max_characters": 0}])
def test_invalid_budgets_fail_before_generation(character, limits):
    with pytest.raises(ValueError, match="Context limits"):
        build_context(character, [], "现在", **limits)


def test_single_turn_service_honors_budget_and_reports_safe_failure(character):
    service = ChatService({character.id: character}, MockProvider(), max_context_characters=1)
    with pytest.raises(ProviderError) as captured:
        service.reply(ChatRequest(character_id=character.id, message="现在"))
    assert captured.value.code == "context_budget_exceeded"
    assert captured.value.http_status == 422


def test_single_turn_service_suppresses_unknown_provider_exception_details(character):
    class BrokenProvider:
        mode = "http"

        def generate(self, request):
            raise RuntimeError("private-secret provider diagnostic")

    service = ChatService({character.id: character}, BrokenProvider())
    with pytest.raises(ProviderError) as captured:
        service.reply(ChatRequest(character_id=character.id, message="现在"))
    assert captured.value.code == "provider_error"
    assert "private-secret" not in str(captured.value)
    assert captured.value.__suppress_context__
