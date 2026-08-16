from __future__ import annotations

from contextlib import asynccontextmanager
import os

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from .api import router
from .engine_adapter import VoiceEngine
from .services import ApplicationServices, EngineFactory
from .settings import Settings


@asynccontextmanager
async def lifespan(app: FastAPI):
    services: ApplicationServices = app.state.services
    services.start()
    try:
        yield
    finally:
        services.stop()


def create_app(
    settings: Settings | None = None,
    engine_factory: EngineFactory = VoiceEngine,
    seed_legacy: bool = True,
) -> FastAPI:
    services = ApplicationServices(
        settings or Settings.from_file(),
        engine_factory=engine_factory,
        seed_legacy=seed_legacy,
    )
    application = FastAPI(title="Voice Lab", version="2.0.0", lifespan=lifespan)
    application.state.services = services
    _configure_http(application, services)
    return application


def _configure_http(application: FastAPI, services: ApplicationServices) -> None:
    application.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=False,
        allow_methods=["GET", "POST", "PATCH", "DELETE"],
        allow_headers=["*"],
    )
    application.mount(
        "/static",
        StaticFiles(directory=services.settings.static_root),
        name="static",
    )
    application.mount(
        "/assets",
        StaticFiles(directory=services.settings.static_root),
        name="assets",
    )
    application.include_router(router)


def _env_flag(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


app = create_app(seed_legacy=_env_flag("VOICE_LAB_SEED_LEGACY", True))
