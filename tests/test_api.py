import pytest
from fastapi.testclient import TestClient

from ai_character_chat.app import create_app


@pytest.fixture
def client():
    with TestClient(create_app()) as test_client:
        yield test_client


def test_health_is_explicitly_mock(client):
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "mode": "mock"}


def test_character_card_is_available(client):
    response = client.get("/api/characters")
    assert response.status_code == 200
    cards = response.json()
    assert len(cards) == 1
    assert cards[0]["id"] == "lumi"
    assert cards[0]["persona"] and cards[0]["greeting"]


def test_reply_is_a_labeled_echo(client):
    response = client.post(
        "/api/chat", json={"character_id": "lumi", "message": "你好"}
    )
    assert response.status_code == 200
    assert response.json() == {
        "character_id": "lumi",
        "reply": "[离线模拟｜露米] 收到：你好",
        "mode": "mock",
    }


def test_unknown_character(client):
    response = client.post(
        "/api/chat", json={"character_id": "missing", "message": "你好"}
    )
    assert response.status_code == 404


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"character_id": "lumi"},
        {"character_id": "", "message": "你好"},
        {"character_id": "lumi", "message": ""},
        {"character_id": "lumi", "message": " \n\t "},
        {"character_id": "lumi", "message": "a" * 2001},
        {"character_id": "lumi", "message": 42},
        {"character_id": "lumi", "message": "你好", "unexpected": True},
    ],
)
def test_invalid_input(client, payload):
    response = client.post("/api/chat", json=payload)
    assert response.status_code == 422


def test_input_trims_outer_whitespace_but_keeps_internal_text(client):
    response = client.post(
        "/api/chat", json={"character_id": " lumi ", "message": "  第一行\n第二行  "}
    )
    assert response.json()["reply"] == "[离线模拟｜露米] 收到：第一行\n第二行"


def test_provider_can_be_replaced_without_changing_routes():
    class ExampleProvider:
        mode = "test"

        def generate(self, character, message):
            return f"{character.id}:{message}"

    with TestClient(create_app(provider=ExampleProvider())) as client:
        response = client.post(
            "/api/chat", json={"character_id": "lumi", "message": "adapter"}
        )
    assert response.json() == {
        "character_id": "lumi", "reply": "lumi:adapter", "mode": "test"
    }


def test_api_docs_and_schema_are_available(client):
    assert client.get("/docs").status_code == 200
    schema = client.get("/openapi.json").json()
    assert "/api/chat" in schema["paths"]
