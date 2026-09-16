from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.core.config import Settings, settings
from app.core.logging import configure_logging
from app.dependencies.resources import AppResources, build_resources
from app.exceptions.errors import register_exception_handlers
from app.middleware.request_logging import RequestLoggingMiddleware
from app.routers import annotations, auth, slides, storage, upload, users

configure_logging(settings.LOG_LEVEL)


def create_app(resources_factory: Callable[[Settings], AppResources] = build_resources) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        # Infrastructure only: no slide is opened or selected at startup.
        resources = resources_factory(settings)
        app.state.resources = resources
        try:
            yield
        finally:
            resources.close()

    app = FastAPI(
        title=settings.PROJECT_NAME,
        openapi_url=f"{settings.API_V1_STR}/openapi.json",
        lifespan=lifespan,
    )

    app.add_middleware(RequestLoggingMiddleware)

    if settings.BACKEND_CORS_ORIGINS:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=[str(origin).rstrip("/") for origin in settings.BACKEND_CORS_ORIGINS],
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
        )

    register_exception_handlers(app)

    app.include_router(auth.router, prefix=f"{settings.API_V1_STR}/auth", tags=["Auth"])
    app.include_router(users.router, prefix=f"{settings.API_V1_STR}/users", tags=["Users"])
    app.include_router(upload.router, prefix=f"{settings.API_V1_STR}/upload", tags=["Upload"])
    app.include_router(slides.router, prefix=f"{settings.API_V1_STR}/slides", tags=["Slides"])
    app.include_router(annotations.router, prefix=settings.API_V1_STR, tags=["Annotations"])
    app.include_router(storage.router, prefix=f"{settings.API_V1_STR}/storage", tags=["Storage"])

    @app.get("/health", tags=["Health"])
    def health_check() -> dict[str, str]:
        return {"status": "ok", "environment": settings.ENVIRONMENT}

    return app


app = create_app()
