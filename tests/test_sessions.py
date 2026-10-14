"""Session behavior, including real persistence and overlapping requests."""

from concurrent.futures import ThreadPoolExecutor
import importlib
import sqlite3
from threading import Event
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from ai_character_chat.app import create_app
from ai_character_chat.providers import GenerationResult, ProviderError
from ai_character_chat.settings import Settings


class CapturingProvider:
    mode = "test"

    def __init__(self):
        self.requests = []

    def generate(self, request):
        self.requests.append(request)
        return GenerationResult(
            reply=f"reply:{request.messages[-1].content}",
            mode=self.mode,
            model="capture-model",
            usage={"prompt_tokens": 12, "completion_tokens": 3, "total_tokens": 15},
            elapsed_ms=4,
        )


@pytest.fixture
def settings(tmp_path):
    return Settings(database_path=str(tmp_path / "chat.sqlite3"))


@pytest.fixture
def provider():
    return CapturingProvider()


@pytest.fixture
def client(settings, provider):
    with TestClient(create_app(provider=provider, settings=settings)) as test_client:
        yield test_client


def new_session(client, character_id="lumi"):
    response = client.post("/api/sessions", json={"character_id": character_id})
    assert response.status_code == 201
    return response.json()


def send(client, session_id, text, request_id=None):
    return client.post(
        f"/api/sessions/{session_id}/messages",
        json={"message": text, "request_id": request_id or str(uuid4())},
    )


def test_session_lifecycle_stores_only_complete_turns(client, provider, settings):
    session = new_session(client)
    session_id = session["id"]
    assert session["character_id"] == "lumi"
    assert session["character_name"] == "露米"
    assert session["created_at"] and session["updated_at"]
    assert len(session["messages"]) == 1
    greeting = session["messages"][0]
    assert greeting["role"] == "assistant" and greeting["request_id"] is None

    request_id = str(uuid4())
    response = send(client, session_id, "  第一轮  ", request_id)
    assert response.status_code == 200
    result = response.json()
    assert result["elapsed_ms"] >= 0
    assert {key: value for key, value in result.items() if key != "elapsed_ms"} == {
        "session_id": session_id,
        "request_id": request_id,
        "character_id": "lumi",
        "reply": "reply:第一轮",
        "mode": "test",
        "model": "capture-model",
        "usage": {"prompt_tokens": 12, "completion_tokens": 3, "total_tokens": 15},
    }
    stored = client.get(f"/api/sessions/{session_id}").json()["messages"]
    assert [(m["role"], m["content"]) for m in stored] == [
        ("assistant", greeting["content"]),
        ("user", "第一轮"),
        ("assistant", "reply:第一轮"),
    ]
    assert [m["request_id"] for m in stored] == [None, request_id, request_id]
    summaries = client.get("/api/sessions?character_id=lumi").json()
    assert [s["id"] for s in summaries] == [session_id]
    assert client.get("/api/sessions?character_id=missing").json() == []

    trace = client.get(f"/api/sessions/{session_id}/traces/{request_id}").json()
    assert trace["status"] == "success" and trace["error_code"] is None
    assert trace["messages"] == [
        {"role": m.role, "content": m.content} for m in provider.requests[-1].messages
    ]
    assert trace["context"]["available_turns"] == 0
    assert trace["context"]["included_turns"] == 0
    assert trace["context"]["truncated"] is False
    assert trace["context"]["characters"] == sum(
        len(m.content) for m in provider.requests[-1].messages
    )
    assert trace["model"] == "capture-model" and trace["usage"] == result["usage"]

    assert client.delete(f"/api/sessions/{session_id}").status_code == 204
    assert client.get(f"/api/sessions/{session_id}").status_code == 404
    assert client.get(f"/api/sessions/{session_id}/traces/{request_id}").status_code == 404
    assert client.get("/api/sessions").json() == []
    with sqlite3.connect(settings.database_path) as database:
        assert database.execute("SELECT COUNT(*) FROM sessions").fetchone()[0] == 0
        assert database.execute("SELECT COUNT(*) FROM messages").fetchone()[0] == 0
        assert database.execute("SELECT COUNT(*) FROM traces").fetchone()[0] == 0


def test_model_receives_role_greeting_and_successful_history(client, provider):
    session = new_session(client)
    assert send(client, session["id"], "one").status_code == 200
    assert send(client, session["id"], "two").status_code == 200
    request = provider.requests[-1]
    assert request.character.id == "lumi"
    assert request.character.persona in request.messages[0].content
    assert request.messages[0].role == "system"
    assert [(m.role, m.content) for m in request.messages[1:]] == [
        ("assistant", session["messages"][0]["content"]),
        ("user", "one"),
        ("assistant", "reply:one"),
        ("user", "two"),
    ]


def test_session_survives_new_application(settings):
    with TestClient(create_app(settings=settings)) as first:
        session = new_session(first)
        request_id = str(uuid4())
        result = send(first, session["id"], "persist", request_id).json()
    with TestClient(create_app(settings=settings)) as restarted:
        restored = restarted.get(f"/api/sessions/{session['id']}").json()
        assert [m["content"] for m in restored["messages"]][1:] == [
            "persist", "[离线模拟｜露米] 收到：persist"
        ]
        assert send(restarted, session["id"], "persist", request_id).json() == result
        assert restarted.get(
            f"/api/sessions/{session['id']}/traces/{request_id}"
        ).status_code == 200


def test_other_sessions_do_not_enter_context(client, provider):
    first, second = new_session(client), new_session(client)
    assert send(client, first["id"], "private first").status_code == 200
    assert send(client, second["id"], "private second").status_code == 200
    assert "private first" not in "\n".join(
        m.content for m in provider.requests[-1].messages
    )
    assert len(client.get(f"/api/sessions/{first['id']}").json()["messages"]) == 3
    assert len(client.get(f"/api/sessions/{second['id']}").json()["messages"]) == 3


def test_successful_request_replays_without_generation_and_rejects_new_text(client, provider):
    session = new_session(client)
    request_id = str(uuid4())
    first = send(client, session["id"], "same", request_id)
    repeat = send(client, session["id"], "same", request_id)
    assert first.status_code == repeat.status_code == 200
    assert repeat.json() == first.json()
    conflict = send(client, session["id"], "different", request_id)
    assert conflict.status_code == 409
    assert conflict.json()["detail"]["code"]
    assert len(provider.requests) == 1
    assert len(client.get(f"/api/sessions/{session['id']}").json()["messages"]) == 3


def test_failed_generation_has_trace_without_half_turn_and_can_retry(settings):
    class FailOnce(CapturingProvider):
        def generate(self, request):
            if not self.requests:
                self.requests.append(request)
                raise ProviderError("timeout", "Model request timed out", http_status=504)
            return super().generate(request)

    provider = FailOnce()
    with TestClient(create_app(provider=provider, settings=settings)) as client:
        session = new_session(client)
        request_id = str(uuid4())
        failed = send(client, session["id"], "retry me", request_id)
        assert failed.status_code == 504
        assert failed.json()["detail"]["code"] == "timeout"
        assert len(client.get(f"/api/sessions/{session['id']}").json()["messages"]) == 1
        trace = client.get(f"/api/sessions/{session['id']}/traces/{request_id}").json()
        assert trace["status"] == "error" and trace["error_code"] == "timeout"
        assert trace["messages"][-1] == {"role": "user", "content": "retry me"}
        assert send(client, session["id"], "retry me", request_id).status_code == 200
        assert len(client.get(f"/api/sessions/{session['id']}").json()["messages"]) == 3


def test_busy_session_blocks_sending_and_deleting_but_other_session_works(settings):
    class BlockingProvider(CapturingProvider):
        def __init__(self):
            super().__init__()
            self.entered = Event()
            self.release = Event()

        def generate(self, request):
            if request.messages[-1].content == "block":
                self.entered.set()
                if not self.release.wait(timeout=10):
                    raise RuntimeError("Test failed to release blocked provider")
            return super().generate(request)

    provider = BlockingProvider()
    with TestClient(create_app(provider=provider, settings=settings)) as client:
        first, second = new_session(client), new_session(client)
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(send, client, first["id"], "block")
            try:
                assert provider.entered.wait(timeout=5)
                # Inference must not keep a SQLite writer transaction open.
                with sqlite3.connect(settings.database_path, timeout=0.1) as database:
                    database.execute("BEGIN IMMEDIATE")
                    database.rollback()
                assert send(client, first["id"], "overlap").status_code == 409
                assert client.delete(f"/api/sessions/{first['id']}").status_code == 409
                assert send(client, second["id"], "parallel").status_code == 200
            finally:
                provider.release.set()
            assert future.result(timeout=5).status_code == 200


def test_history_limit_excludes_complete_turns_without_deleting_storage(settings, provider):
    settings = settings.model_copy(update={"max_history_turns": 1})
    with TestClient(create_app(provider=provider, settings=settings)) as client:
        session = new_session(client)
        assert send(client, session["id"], "one").status_code == 200
        assert send(client, session["id"], "two").status_code == 200
        request_id = str(uuid4())
        assert send(client, session["id"], "three", request_id).status_code == 200
        messages = provider.requests[-1].messages
        assert [(m.role, m.content) for m in messages[2:]] == [
            ("user", "two"), ("assistant", "reply:two"), ("user", "three")
        ]
        trace = client.get(f"/api/sessions/{session['id']}/traces/{request_id}").json()
        assert trace["context"]["available_turns"] == 2
        assert trace["context"]["included_turns"] == 1
        assert trace["context"]["truncated"] is True
        assert len(client.get(f"/api/sessions/{session['id']}").json()["messages"]) == 7


def test_existing_session_uses_original_character_snapshot(settings, monkeypatch):
    app_module = importlib.import_module("ai_character_chat.app")
    original_cards = app_module.load_characters()
    old_provider = CapturingProvider()
    with TestClient(create_app(provider=old_provider, settings=settings)) as client:
        original_session = new_session(client)
    changed_cards = {
        key: value.model_copy(update={"persona": "完全不同的新设定"})
        for key, value in original_cards.items()
    }
    monkeypatch.setattr(app_module, "load_characters", lambda: changed_cards)
    new_provider = CapturingProvider()
    with TestClient(create_app(provider=new_provider, settings=settings)) as client:
        assert send(client, original_session["id"], "old").status_code == 200
        assert new_provider.requests[-1].character.persona == original_cards["lumi"].persona
        new = new_session(client)
        assert send(client, new["id"], "new").status_code == 200
        assert new_provider.requests[-1].character.persona == "完全不同的新设定"


def test_character_sessions_use_their_own_snapshot_and_greeting(settings, monkeypatch):
    app_module = importlib.import_module("ai_character_chat.app")
    cards = app_module.load_characters()
    other = cards["lumi"].model_copy(update={
        "id": "other", "name": "另一角色", "persona": "另一种身份",
        "greeting": "另一个开场白",
    })
    monkeypatch.setattr(app_module, "load_characters", lambda: {**cards, "other": other})
    provider = CapturingProvider()
    with TestClient(create_app(provider=provider, settings=settings)) as client:
        first, second = new_session(client), new_session(client, "other")
        assert send(client, first["id"], "露米的消息").status_code == 200
        assert send(client, second["id"], "另一角色的消息").status_code == 200
        request = provider.requests[-1]
        assert request.character.id == "other"
        assert request.messages[1].content == "另一个开场白"
        assert all("露米的消息" not in m.content for m in request.messages)
        assert [s["id"] for s in client.get("/api/sessions?character_id=other").json()] == [
            second["id"]
        ]


def test_impossible_context_budget_returns_error_without_calling_provider(settings, provider):
    settings = settings.model_copy(update={"max_context_characters": 1})
    with TestClient(create_app(provider=provider, settings=settings)) as client:
        session = new_session(client)
        request_id = str(uuid4())
        response = send(client, session["id"], "必需内容不能静默丢弃", request_id)
        assert response.status_code == 422
        assert response.json()["detail"]["code"] == "context_budget_exceeded"
        assert provider.requests == []
        assert len(client.get(f"/api/sessions/{session['id']}").json()["messages"]) == 1
        trace = client.get(f"/api/sessions/{session['id']}/traces/{request_id}").json()
        assert trace["status"] == "error" and trace["error_code"] == "context_budget_exceeded"


def test_unexpected_provider_error_does_not_expose_details(settings):
    secret = "private-key-and-upstream-raw-body"

    class UnexpectedErrorProvider(CapturingProvider):
        def generate(self, request):
            raise RuntimeError(secret)

    with TestClient(create_app(provider=UnexpectedErrorProvider(), settings=settings)) as client:
        session = new_session(client)
        request_id = str(uuid4())
        response = send(client, session["id"], "hello", request_id)
        assert response.status_code == 502
        assert response.json()["detail"]["code"] == "provider_error"
        trace = client.get(f"/api/sessions/{session['id']}/traces/{request_id}")
        assert secret not in response.text and secret not in trace.text
        assert len(client.get(f"/api/sessions/{session['id']}").json()["messages"]) == 1


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"message": "hello"},
        {"message": "hello", "request_id": "not-a-uuid"},
        {"message": "hello", "request_id": 42},
        {"message": " \n\t ", "request_id": str(uuid4())},
        {"message": "a" * 2001, "request_id": str(uuid4())},
        {"message": 42, "request_id": str(uuid4())},
        {"message": "hello", "request_id": str(uuid4()), "extra": True},
    ],
)
def test_invalid_session_message(client, payload):
    session = new_session(client)
    response = client.post(f"/api/sessions/{session['id']}/messages", json=payload)
    assert response.status_code == 422
    assert len(client.get(f"/api/sessions/{session['id']}").json()["messages"]) == 1


def test_unknown_resources_and_character(client):
    assert client.post("/api/sessions", json={"character_id": "unknown"}).status_code == 404
    missing = str(uuid4())
    assert client.get(f"/api/sessions/{missing}").status_code == 404
    assert client.delete(f"/api/sessions/{missing}").status_code == 404
    assert send(client, missing, "hello").status_code == 404
    session = new_session(client)
    assert client.get(f"/api/sessions/{session['id']}/traces/{missing}").status_code == 404


def test_legacy_single_turn_does_not_save_session_or_history(client, provider):
    for text in ("old one", "old two"):
        response = client.post("/api/chat", json={"character_id": "lumi", "message": text})
        assert response.status_code == 200
        assert set(response.json()) == {"character_id", "reply", "mode"}
    assert client.get("/api/sessions").json() == []
    assert [m.content for m in provider.requests[-1].messages if m.role == "user"] == [
        "old two"
    ]
