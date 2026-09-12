from pathlib import Path
from typing import Annotated, Any

from pydantic import AnyUrl, BeforeValidator, PostgresDsn
from pydantic_settings import BaseSettings, SettingsConfigDict


def parse_cors(v: Any) -> list[str] | str:
    if isinstance(v, str) and not v.startswith("["):
        return [i.strip() for i in v.split(",")]
    elif isinstance(v, (list, str)):
        return v
    raise ValueError(v)


class Settings(BaseSettings):
    ENVIRONMENT: str = "development"
    PROJECT_NAME: str = "Backend API"
    API_V1_STR: str = "/api/v1"
    BACKEND_CORS_ORIGINS: Annotated[list[AnyUrl] | str, BeforeValidator(parse_cors)] = []
    S3_ENDPOINT: str
    # Endpoint browsers use for presigned URLs (defaults to S3_ENDPOINT).
    S3_PUBLIC_ENDPOINT: str | None = None
    S3_ACCESS_KEY: str
    S3_SECRET_KEY: str
    S3_BUCKET_NAME: str
    DATABASE_URL: PostgresDsn
    # Local, re-creatable copies of slides (OpenSlide needs filesystem paths).
    SLIDE_CACHE_DIR: Path = Path(".slide-cache")

    model_config = SettingsConfigDict(env_file=".env.local", env_file_encoding="utf-8", extra="ignore")


settings = Settings()
