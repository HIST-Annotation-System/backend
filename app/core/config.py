from pathlib import Path
from typing import Annotated, Any

from pydantic import AnyUrl, BeforeValidator, PostgresDsn, computed_field
from pydantic_core import MultiHostUrl
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
    LOG_LEVEL: str = "INFO"
    BACKEND_CORS_ORIGINS: Annotated[list[AnyUrl] | str, BeforeValidator(parse_cors)] = []
    BUCKET_ENDPOINT: str
    BUCKET_ACCESS_KEY: str
    BUCKET_SECRET_KEY: str
    BUCKET_NAME: str
    BUCKET_REGION: str
    # The endpoint the *browser* reaches the bucket at, if not BUCKET_ENDPOINT: presigned URLs are signed for it. Only
    # differs locally, where the API container reaches the S3 emulator by its service name.
    BUCKET_PUBLIC_ENDPOINT: str | None = None
    
    POSTGRES_SERVER: str = "localhost"
    POSTGRES_PORT: int = 5432
    POSTGRES_USER: str
    POSTGRES_PASSWORD: str
    POSTGRES_DB: str
    # Local, re-creatable copies of slides (OpenSlide needs filesystem paths).
    SLIDE_CACHE_DIR: Path = Path(".slide-cache")

    # Off unless this machine runs the ml-worker stack: hotspots then stay `pending` and everything else works.
    SEGMENTATION_ENABLED: bool = False
    # Moving to SQS only changes these two (plus AWS credentials); see README.md.
    CELERY_BROKER_URL: str = "redis://host.docker.internal:6379/0"
    CELERY_BROKER_TRANSPORT_OPTIONS: dict[str, Any] = {"visibility_timeout": 3600}

    JWT_SECRET: str
    JWT_ALGORITHM: str
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60
    # True wherever the site is served over HTTPS (prod): the browser then sends the auth cookies over HTTPS only.
    COOKIE_SECURE: bool = False
    REFRESH_TOKEN_EXPIRE_DAYS: int = 7
    # The administrator account created by scripts/create_admin.py on first start.
    ADMIN_USERNAME: str
    ADMIN_PASSWORD: str

    model_config = SettingsConfigDict(env_file=".env.local", env_file_encoding="utf-8", extra="ignore")

    @computed_field  # type: ignore[prop-decorator]
    @property
    def DATABASE_URL(self) -> PostgresDsn:
        return MultiHostUrl.build(  # type: ignore[return-value]
            scheme="postgresql+psycopg",
            username=self.POSTGRES_USER,
            password=self.POSTGRES_PASSWORD,
            host=self.POSTGRES_SERVER,
            port=self.POSTGRES_PORT,
            path=self.POSTGRES_DB,
        )


settings = Settings()
