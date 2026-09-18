from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.core.config import settings
from app.core.logging import configure_logging
from app.dependencies.resources import build_resources
from app.exceptions.errors import register_exception_handlers
from app.middleware.request_logging import RequestLoggingMiddleware
from app.services.slide_ingest import fail_interrupted_ingests
from app.routers import annotations, auth, categories, groups, slides, upload, users

configure_logging(settings.LOG_LEVEL)

def create_app() -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncGenerator[None]:
        # Infrastructure only: no slide is opened or selected at startup.
        resources = build_resources(settings)
        fail_interrupted_ingests(resources.session_factory)
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
    app.include_router(categories.router, prefix=f"{settings.API_V1_STR}/categories", tags=["Categories"])
    app.include_router(groups.router, prefix=f"{settings.API_V1_STR}/groups", tags=["Groups"])
    app.include_router(upload.router, prefix=f"{settings.API_V1_STR}/upload", tags=["Upload"])
    app.include_router(slides.router, prefix=f"{settings.API_V1_STR}/slides", tags=["Slides"])
    app.include_router(annotations.router, prefix=settings.API_V1_STR, tags=["Annotations"])

    @app.get("/health", tags=["Health"])
    def health_check() -> dict[str, str]:
        return {"status": "ok", "environment": settings.ENVIRONMENT}

    return app


app = create_app()
