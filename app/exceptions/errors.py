import logging

from fastapi import FastAPI, Request, status
from fastapi.responses import JSONResponse

logger = logging.getLogger("app.errors")


class AppError(Exception):
    """Base class for errors raised by services; the API answers with `status_code` and `{detail}`."""

    status_code = status.HTTP_400_BAD_REQUEST
    # Extra response headers, e.g. Retry-After.
    headers: dict[str, str] | None = None

    def __init__(self, message: str, code: str | None = None) -> None:
        super().__init__(message)
        self.message = message
        # Machine-readable reason, for clients that react to a specific error (e.g. an edit conflict).
        self.code = code


class NotFoundError(AppError):
    status_code = status.HTTP_404_NOT_FOUND


class ConflictError(AppError):
    status_code = status.HTTP_409_CONFLICT


class ForbiddenError(AppError):
    status_code = status.HTTP_403_FORBIDDEN


class InvalidInputError(AppError):
    status_code = status.HTTP_422_UNPROCESSABLE_CONTENT


class SlidePreparingError(AppError):
    """The slide is being copied to this server's disk in the background; the client asks again shortly."""

    status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    # no-store: a CDN in front must not keep answering 503 once the slide is ready.
    headers = {"Retry-After": "5", "Cache-Control": "no-store"}


class TooManyRequestsError(AppError):
    status_code = status.HTTP_429_TOO_MANY_REQUESTS

    def __init__(self, message: str, retry_after_seconds: int, code: str | None = None) -> None:
        super().__init__(message, code)
        self.headers = {"Retry-After": str(retry_after_seconds)}


class InsufficientStorageError(AppError):
    status_code = status.HTTP_507_INSUFFICIENT_STORAGE


class StorageError(AppError):
    status_code = status.HTTP_502_BAD_GATEWAY


class StorageObjectMissing(StorageError):
    """The object (or multipart upload) is not in the bucket: the storage itself answered."""

    status_code = status.HTTP_404_NOT_FOUND


def _app_error_handler(request: Request, exc: Exception) -> JSONResponse:
    if not isinstance(exc, AppError):
        raise exc
    logger.warning(
        "request rejected: %s",
        exc.message,
        extra={"path": request.url.path, "status_code": exc.status_code},
    )
    content = {"detail": exc.message} if exc.code is None else {"detail": exc.message, "code": exc.code}
    return JSONResponse(status_code=exc.status_code, content=content, headers=exc.headers)


def register_exception_handlers(app: FastAPI) -> None:
    app.add_exception_handler(AppError, _app_error_handler)
