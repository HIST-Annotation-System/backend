import asyncio

from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

SERVER_BUSY = "Server busy, try again."
# CloudFront gives up on the server after 60 s; a request still queued after this long is dropped instead of being
# worked through for a client that has likely left, so an overload clears within seconds of ending.
QUEUE_TIMEOUT_S = 20
RETRY_AFTER_S = "5"
HEALTH_PATH = "/health"


class ConcurrencyLimitMiddleware:
    """Runs at most `limit` HTTP requests at once; the rest wait their turn on the event loop, holding no thread and no
    database connection, and get a 503 if their turn does not come within `queue_timeout` seconds."""

    def __init__(self, app: ASGIApp, limit: int, queue_timeout: float = QUEUE_TIMEOUT_S) -> None:
        self.app = app
        self.slots = asyncio.Semaphore(limit)
        self.queue_timeout = queue_timeout

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        # The health check uses no database, and must keep answering while the server is busy.
        if scope["type"] != "http" or scope["path"] == HEALTH_PATH:
            await self.app(scope, receive, send)
            return
        try:
            async with asyncio.timeout(self.queue_timeout):
                await self.slots.acquire()
        except TimeoutError:
            response = JSONResponse({"detail": SERVER_BUSY}, status_code=503, headers={"Retry-After": RETRY_AFTER_S})
            await response(scope, receive, send)
            return
        try:
            await self.app(scope, receive, send)
        finally:
            self.slots.release()
