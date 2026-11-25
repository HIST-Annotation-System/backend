"""Conditional GET (ETag / If-None-Match) and If-Match parsing for JSON resources that clients poll."""

import hashlib
import json
from typing import Any

from fastapi import Request, Response, status

from app.core.constants import MEDIA_TYPE_JSON

ETAG_HASH_LENGTH = 32
# Revalidate on every use: the browser then sends If-None-Match by itself and turns a 304 back into
# the cached body, so polling clients get unchanged lists for a few bytes without any extra code.
REVALIDATE_ALWAYS = "private, no-cache"


def _strip_etag(value: str) -> str:
    value = value.strip()
    return value.removeprefix("W/").strip('"')


def parse_if_match(header: str | None) -> str | None:
    """The opaque value of a single-entity `If-Match` header ("*" or absent means: don't check)."""
    if header is None or header.strip() in ("", "*"):
        return None
    return _strip_etag(header.split(",")[0])


def etag_json_response(request: Request, content: Any) -> Response:
    """`content` (already JSON-serializable) as JSON with a content-hash ETag; 304 if the client has it."""
    body = json.dumps(content, separators=(",", ":"), ensure_ascii=False).encode()
    etag = hashlib.sha256(body).hexdigest()[:ETAG_HASH_LENGTH]
    headers = {"ETag": f'"{etag}"', "Cache-Control": REVALIDATE_ALWAYS}
    known = {_strip_etag(tag) for tag in request.headers.get("if-none-match", "").split(",") if tag.strip()}
    if etag in known:
        return Response(status_code=status.HTTP_304_NOT_MODIFIED, headers=headers)
    return Response(body, media_type=MEDIA_TYPE_JSON, headers=headers)
