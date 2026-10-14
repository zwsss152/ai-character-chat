"""Coordinate local multi-turn sessions without holding SQL transactions during inference."""

from threading import Lock
from time import perf_counter
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from .characters import Character
from .chat import CharacterId, Message, UnknownCharacterError
from .context import ContextBudgetError, build_context
from .providers import ChatProvider, GenerationRequest, ModelMessage, ProviderError
from .settings import Settings
from .storage import SessionStore, utc_now


class CreateSessionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    character_id: CharacterId


class SessionMessageRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    message: Message
    request_id: UUID


class SessionSummary(BaseModel):
    id: str
    character_id: str
    character_name: str
    created_at: str
    updated_at: str


class StoredMessage(BaseModel):
    role: Literal["user", "assistant"]
    content: str
    request_id: str | None


class SessionDetail(SessionSummary):
    messages: list[StoredMessage]


class TurnResponse(BaseModel):
    session_id: str
    request_id: str
    character_id: str
    reply: str
    mode: str
    model: str
    usage: dict[str, int] | None
    elapsed_ms: float


class SessionConflictError(Exception):
    def __init__(self, code: str, message: str):
        self.code = code
        self.message = message


class SessionService:
    def __init__(self, characters: dict[str, Character], provider: ChatProvider,
                 settings: Settings, store: SessionStore):
        self.characters = characters
        self.provider = provider
        self.settings = settings
        self.store = store
        self._locks: dict[str, Lock] = {}
        self._registry_lock = Lock()

    def _lock(self, session_id: str) -> Lock:
        with self._registry_lock:
            return self._locks.setdefault(session_id, Lock())

    def create(self, request: CreateSessionRequest) -> dict:
        character = self.characters.get(request.character_id)
        if character is None:
            raise UnknownCharacterError(request.character_id)
        return self.store.create(character)

    def _acquire(self, session_id: str) -> Lock:
        # Avoid allocating locks for arbitrary nonexistent identifiers.
        self.store.get(session_id)
        lock = self._lock(session_id)
        if not lock.acquire(blocking=False):
            raise SessionConflictError("session_busy", "当前会话正在生成回复，请稍后重试。")
        return lock

    def delete(self, session_id: str) -> None:
        lock = self._acquire(session_id)
        try:
            self.store.delete(session_id)
        finally:
            lock.release()

    def send(self, session_id: str, request: SessionMessageRequest) -> dict:
        lock = self._acquire(session_id)
        try:
            return self._send_locked(session_id, request)
        finally:
            lock.release()

    def _send_locked(self, session_id: str, request: SessionMessageRequest) -> dict:
        request_id = str(request.request_id)
        session = self.store.get(session_id)
        previous = self.store.previous_request(session_id, request_id)
        if previous is not None:
            if previous["message"] != request.message:
                raise SessionConflictError("request_id_conflict", "请求标识已用于另一条消息。")
            if previous["result"] is not None:
                return previous["result"]

        character = self.store.character(session_id)
        history = [ModelMessage(role=item["role"], content=item["content"])
                   for item in session["messages"][1:]]
        trace = {
            "session_id": session_id, "request_id": request_id,
            "status": "error", "mode": self.provider.mode,
            "model": getattr(self.provider, "model", self.provider.mode),
            "created_at": utc_now(), "elapsed_ms": 0.0, "usage": None,
            "messages": [], "context": {"available_turns": len(history) // 2,
                "included_turns": 0, "truncated": False, "characters": 0},
            "error_code": None,
        }
        started = perf_counter()
        try:
            context = build_context(
                character, history, request.message,
                max_turns=self.settings.max_history_turns,
                max_characters=self.settings.max_context_characters,
            )
            trace["messages"] = [item.model_dump() for item in context.messages]
            trace["context"] = context.model_dump(exclude={"messages"})
            generation = self.provider.generate(
                GenerationRequest(character=character, messages=context.messages)
            )
            result = TurnResponse(
                session_id=session_id, request_id=request_id, character_id=character.id,
                reply=generation.reply, mode=generation.mode, model=generation.model,
                usage=generation.usage, elapsed_ms=round((perf_counter() - started) * 1000, 3),
            ).model_dump()
            trace.update(status="success", mode=generation.mode, model=generation.model,
                         usage=generation.usage, elapsed_ms=result["elapsed_ms"])
        except ContextBudgetError:
            error = ProviderError("context_budget_exceeded", "角色设定与当前输入超过上下文字符预算。", 422)
            self._record_failure(session_id, request, trace, error, started)
            raise error from None
        except ProviderError as error:
            self._record_failure(session_id, request, trace, error, started)
            raise
        except Exception:
            # No exception details from arbitrary providers reach a client or trace.
            error = ProviderError("provider_error", "生成回复失败，请稍后重试。")
            self._record_failure(session_id, request, trace, error, started)
            raise error from None

        self.store.record(session_id, request.message, trace, result)
        return result

    def _record_failure(self, session_id: str, request: SessionMessageRequest,
                        trace: dict, error: ProviderError, started: float) -> None:
        trace["error_code"] = error.code
        trace["elapsed_ms"] = round((perf_counter() - started) * 1000, 3)
        self.store.record(session_id, request.message, trace)
