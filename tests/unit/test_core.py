import json
import logging
import sys
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.requests import Request

from app.core.config import Settings, parse_cors
from app.core.http_cache import etag_json_response, parse_if_match
from app.core.logging import JsonFormatter, request_id_var
from app.exceptions.errors import (
    AppError,
    ConflictError,
    ForbiddenError,
    InvalidInputError,
    NotFoundError,
    StorageError,
    register_exception_handlers,
)


class TestParseIfMatch:
    @pytest.mark.parametrize("header", [None, "", "  ", "*", " * "])
    def test_absent_or_wildcard_means_no_check(self, header: str | None) -> None:
        assert parse_if_match(header) is None

    @pytest.mark.parametrize(("header", "expected"), [('"abc"', "abc"), ('W/"abc"', "abc"), ("abc", "abc")])
    def test_strips_quotes_and_weak_prefix(self, header: str, expected: str) -> None:
        assert parse_if_match(header) == expected

    def test_list_uses_the_first_tag(self) -> None:
        assert parse_if_match('"one", "two"') == "one"


def request_with(headers: dict[str, str] | None = None) -> Request:
    raw = [(k.lower().encode(), v.encode()) for k, v in (headers or {}).items()]
    return Request({"type": "http", "method": "GET", "path": "/", "headers": raw, "query_string": b""})


class TestEtagJsonResponse:
    def test_first_request_gets_the_body_and_an_etag(self) -> None:
        response = etag_json_response(request_with(), {"b": 1, "a": "ż"})
        assert response.status_code == 200
        assert json.loads(response.body) == {"b": 1, "a": "ż"}
        assert response.headers["etag"].startswith('"') and len(response.headers["etag"]) == 34
        assert response.headers["cache-control"] == "private, no-cache"
        assert response.media_type == "application/json"

    def test_same_content_has_the_same_etag(self) -> None:
        first = etag_json_response(request_with(), [1, 2, 3]).headers["etag"]
        assert etag_json_response(request_with(), [1, 2, 3]).headers["etag"] == first
        assert etag_json_response(request_with(), [1, 2, 4]).headers["etag"] != first

    @pytest.mark.parametrize("template", ["{tag}", "W/{tag}", '"other", {tag}'])
    def test_known_etag_gets_304_without_a_body(self, template: str) -> None:
        etag = etag_json_response(request_with(), {"x": 1}).headers["etag"]
        response = etag_json_response(request_with({"If-None-Match": template.format(tag=etag)}), {"x": 1})
        assert response.status_code == 304
        assert response.body == b""
        assert response.headers["etag"] == etag

    def test_stale_etag_gets_the_new_body(self) -> None:
        response = etag_json_response(request_with({"If-None-Match": '"stale"'}), {"x": 1})
        assert response.status_code == 200


class TestConfig:
    @pytest.mark.parametrize(
        ("value", "expected"),
        [
            ("http://a, http://b", ["http://a", "http://b"]),
            ("http://a", ["http://a"]),
            ('["http://a"]', '["http://a"]'),
            (["http://a"], ["http://a"]),
        ],
    )
    def test_parse_cors(self, value: Any, expected: Any) -> None:
        assert parse_cors(value) == expected

    def test_parse_cors_rejects_other_types(self) -> None:
        with pytest.raises(ValueError):
            parse_cors(42)

    def test_database_url_is_built_from_parts(self) -> None:
        settings = Settings(
            _env_file=None,  # type: ignore[call-arg]
            BUCKET_ENDPOINT="e",
            BUCKET_ACCESS_KEY="a",
            BUCKET_SECRET_KEY="s",
            BUCKET_NAME="b",
            BUCKET_REGION="r",
            POSTGRES_SERVER="db",
            POSTGRES_PORT=6543,
            POSTGRES_USER="user",
            POSTGRES_PASSWORD="pw",
            POSTGRES_DB="wsi",
            JWT_SECRET="x",
            JWT_ALGORITHM="HS256",
            ADMIN_USERNAME="admin",
            ADMIN_PASSWORD="admin",
            BACKEND_CORS_ORIGINS="http://localhost:5173,http://127.0.0.1:5173",
        )
        assert str(settings.DATABASE_URL) == "postgresql+psycopg://user:pw@db:6543/wsi"
        assert [str(origin).rstrip("/") for origin in settings.BACKEND_CORS_ORIGINS] == [
            "http://localhost:5173",
            "http://127.0.0.1:5173",
        ]


class TestErrors:
    @pytest.mark.parametrize(
        ("error", "status"),
        [
            (AppError, 400),
            (NotFoundError, 404),
            (ConflictError, 409),
            (ForbiddenError, 403),
            (InvalidInputError, 422),
            (StorageError, 502),
        ],
    )
    def test_each_error_maps_to_its_status(self, error: type[AppError], status: int) -> None:
        app = FastAPI()
        register_exception_handlers(app)

        @app.get("/boom")
        def boom() -> None:
            raise error("went wrong")

        response = TestClient(app).get("/boom")
        assert response.status_code == status
        assert response.json() == {"detail": "went wrong"}

    def test_code_is_included_when_given(self) -> None:
        app = FastAPI()
        register_exception_handlers(app)

        @app.get("/boom")
        def boom() -> None:
            raise ConflictError("changed", code="slide_modified")

        assert TestClient(app).get("/boom").json() == {"detail": "changed", "code": "slide_modified"}

    def test_other_exceptions_are_not_swallowed(self) -> None:
        app = FastAPI()
        register_exception_handlers(app)

        @app.get("/boom")
        def boom() -> None:
            raise RuntimeError("bug")

        assert TestClient(app, raise_server_exceptions=False).get("/boom").status_code == 500


class TestJsonFormatter:
    def record(self, **extra: Any) -> logging.LogRecord:
        record = logging.LogRecord("app.test", logging.WARNING, __file__, 1, "hello %s", ("world",), None)
        record.__dict__.update(extra)
        return record

    def test_outputs_one_json_object(self) -> None:
        payload = json.loads(JsonFormatter().format(self.record()))
        assert payload["level"] == "WARNING"
        assert payload["logger"] == "app.test"
        assert payload["message"] == "hello world"
        assert payload["timestamp"].endswith("+00:00")
        assert "request_id" not in payload

    def test_includes_extra_fields_and_non_json_values_as_strings(self) -> None:
        payload = json.loads(JsonFormatter().format(self.record(path="/x", status_code=404, obj=object())))
        assert payload["path"] == "/x"
        assert payload["status_code"] == 404
        assert payload["obj"].startswith("<object")

    def test_includes_the_request_id_of_the_current_request(self) -> None:
        token = request_id_var.set("req-1")
        try:
            assert json.loads(JsonFormatter().format(self.record()))["request_id"] == "req-1"
        finally:
            request_id_var.reset(token)

    def test_includes_the_exception(self) -> None:
        try:
            raise ValueError("bad")
        except ValueError:
            record = self.record()
            record.exc_info = sys.exc_info()
        assert "ValueError: bad" in json.loads(JsonFormatter().format(record))["exception"]
