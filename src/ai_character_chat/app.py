"""Local HTTP entry point for role dialogue, sessions and inspectable calls."""

from contextlib import asynccontextmanager
from importlib.resources import files
from uuid import UUID

from fastapi import FastAPI, Request, Response
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from .characters import Character, load_characters
from .chat import ChatRequest, ChatResponse, ChatService, UnknownCharacterError
from .providers import ChatProvider, ProviderError
from .sessions import (CreateSessionRequest, SessionConflictError, SessionDetail,
                       SessionMessageRequest, SessionService, SessionSummary, TurnResponse)
from .settings import Settings, make_provider
from .storage import SessionNotFoundError, SessionStore, TraceNotFoundError


def create_app(provider: ChatProvider | None = None,
               settings: Settings | None = None) -> FastAPI:
    selected_settings = settings if settings is not None else Settings.from_env()
    characters = load_characters()
    selected_provider = provider if provider is not None else make_provider(selected_settings)
    service = ChatService(characters, selected_provider,
                          max_context_characters=selected_settings.max_context_characters)
    store = SessionStore(selected_settings.database_path)
    sessions = SessionService(characters, selected_provider, selected_settings, store)

    @asynccontextmanager
    async def lifespan(application: FastAPI):
        try:
            yield
        finally:
            if provider is None and hasattr(selected_provider, "close"):
                selected_provider.close()

    application = FastAPI(
        title="AI Character Chat — Role Dialogue Foundation",
        version="0.3.0",
        description="Local role dialogue with persistent sessions and inspectable model inputs. "
                    "Mock mode is an explicitly labelled echo, not role generation.",
        lifespan=lifespan,
    )
    application.state.session_service = sessions
    application.state.store = store
    web_directory = files("ai_character_chat").joinpath("web")
    application.mount("/assets", StaticFiles(directory=str(web_directory)), name="assets")

    @application.exception_handler(UnknownCharacterError)
    async def unknown_character(request: Request, error: UnknownCharacterError):
        return JSONResponse(status_code=404, content={"detail": {
            "code": "character_not_found", "message": "角色不存在。"}})

    @application.exception_handler(SessionNotFoundError)
    async def unknown_session(request: Request, error: SessionNotFoundError):
        return JSONResponse(status_code=404, content={"detail": {
            "code": "session_not_found", "message": "会话不存在或已删除。"}})

    @application.exception_handler(TraceNotFoundError)
    async def unknown_trace(request: Request, error: TraceNotFoundError):
        return JSONResponse(status_code=404, content={"detail": {
            "code": "trace_not_found", "message": "尚无此请求的调用记录。"}})

    @application.exception_handler(SessionConflictError)
    async def session_conflict(request: Request, error: SessionConflictError):
        return JSONResponse(status_code=409, content={"detail": {
            "code": error.code, "message": error.message}})

    @application.exception_handler(ProviderError)
    async def provider_failed(request: Request, error: ProviderError):
        return JSONResponse(status_code=error.http_status, content={"detail": {
            "code": error.code, "message": error.message}})

    @application.get("/", include_in_schema=False)
    def web_client() -> FileResponse:
        return FileResponse(str(web_directory.joinpath("index.html")))

    @application.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok", "mode": selected_provider.mode}

    @application.get("/api/characters", response_model=list[Character])
    def list_characters() -> list[Character]:
        return list(characters.values())

    @application.post("/api/chat", response_model=ChatResponse)
    def chat(request: ChatRequest) -> ChatResponse:
        return service.reply(request)

    @application.post("/api/sessions", response_model=SessionDetail, status_code=201)
    def create_session(request: CreateSessionRequest):
        return sessions.create(request)

    @application.get("/api/sessions", response_model=list[SessionSummary])
    def list_sessions(character_id: str | None = None):
        return store.list(character_id)

    @application.get("/api/sessions/{session_id}", response_model=SessionDetail)
    def get_session(session_id: UUID):
        return store.get(str(session_id))

    @application.delete("/api/sessions/{session_id}", status_code=204)
    def delete_session(session_id: UUID) -> Response:
        sessions.delete(str(session_id))
        return Response(status_code=204)

    @application.post("/api/sessions/{session_id}/messages", response_model=TurnResponse)
    def send_message(session_id: UUID, request: SessionMessageRequest):
        return sessions.send(str(session_id), request)

    @application.get("/api/sessions/{session_id}/traces/{request_id}")
    def get_trace(session_id: UUID, request_id: UUID):
        return store.trace(str(session_id), str(request_id))

    return application


app = create_app()
