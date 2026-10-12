"""HTTP entry point. Start here when tracing a request."""

from fastapi import FastAPI, HTTPException

from .characters import Character, load_characters
from .chat import ChatRequest, ChatResponse, ChatService, UnknownCharacterError
from .providers import ChatProvider, MockProvider


def create_app(provider: ChatProvider | None = None) -> FastAPI:
    characters = load_characters()
    selected_provider = provider if provider is not None else MockProvider()
    service = ChatService(characters, selected_provider)
    application = FastAPI(
        title="AI Character Chat — Learning Scaffold",
        version="0.1.0",
        description="Offline mock replies only. Real model integration is a later learning task.",
    )

    @application.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok", "mode": selected_provider.mode}

    @application.get("/api/characters", response_model=list[Character])
    def list_characters() -> list[Character]:
        return list(characters.values())

    @application.post("/api/chat", response_model=ChatResponse)
    def chat(request: ChatRequest) -> ChatResponse:
        try:
            return service.reply(request)
        except UnknownCharacterError as error:
            raise HTTPException(status_code=404, detail="Character not found") from error

    return application


app = create_app()
