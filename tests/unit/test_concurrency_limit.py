import asyncio

import httpx
import pytest
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import PlainTextResponse
from starlette.routing import Route

from app.middleware.concurrency_limit import ConcurrencyLimitMiddleware

pytestmark = pytest.mark.anyio


def client_for(limit: int, queue_timeout: float) -> tuple[httpx.AsyncClient, asyncio.Event, list[int]]:
    release = asyncio.Event()
    running = [0, 0]  # now, most at once

    async def slow(request: Request) -> PlainTextResponse:
        running[0] += 1
        running[1] = max(running[1], running[0])
        await release.wait()
        running[0] -= 1
        return PlainTextResponse("ok")

    async def health(request: Request) -> PlainTextResponse:
        return PlainTextResponse("ok")

    app = ConcurrencyLimitMiddleware(
        Starlette(routes=[Route("/slow", slow), Route("/health", health)]), limit=limit, queue_timeout=queue_timeout
    )
    client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")
    return client, release, running


async def test_requests_over_the_limit_wait_their_turn() -> None:
    client, release, running = client_for(limit=2, queue_timeout=5)
    requests = [asyncio.create_task(client.get("/slow")) for _ in range(5)]
    await asyncio.sleep(0.05)
    assert running[0] == 2
    release.set()
    responses = await asyncio.gather(*requests)
    assert [response.status_code for response in responses] == [200] * 5
    assert running[1] == 2


async def test_a_request_queued_too_long_is_turned_away() -> None:
    client, release, _ = client_for(limit=1, queue_timeout=0.05)
    first = asyncio.create_task(client.get("/slow"))
    await asyncio.sleep(0.01)
    busy = await client.get("/slow")
    assert busy.status_code == 503
    assert busy.headers["Retry-After"] == "5"
    release.set()
    assert (await first).status_code == 200


async def test_health_answers_while_every_slot_is_taken() -> None:
    client, release, _ = client_for(limit=1, queue_timeout=0.05)
    first = asyncio.create_task(client.get("/slow"))
    await asyncio.sleep(0.01)
    assert (await client.get("/health")).status_code == 200
    release.set()
    await first
