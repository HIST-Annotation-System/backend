from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from app.dependencies.auth import (
    RequireRole,
    get_current_user,
    get_org_scope,
    require_admin,
    require_manager,
    require_upload,
)
from app.models import Role


def user(role: Role, organization_id: int | None = 1) -> Any:
    return SimpleNamespace(id=1, role=role, organization_id=organization_id)


def request(cookies: dict[str, str] | None = None) -> Request:
    header = "; ".join(f"{k}={v}" for k, v in (cookies or {}).items())
    headers = [(b"cookie", header.encode())] if header else []
    return Request({"type": "http", "method": "GET", "path": "/", "headers": headers, "query_string": b""})


class FakeAuth:
    def __init__(self, valid: dict[str, Any]) -> None:
        self.valid = valid
        self.seen: list[str] = []

    def get_current_user_from_token(self, token: str) -> Any:
        self.seen.append(token)
        return self.valid.get(token)


class TestGetCurrentUser:
    def test_bearer_token(self) -> None:
        account = user(Role.ANNOTATOR)
        assert get_current_user(request(), "tok", FakeAuth({"tok": account})) is account  # type: ignore[arg-type]

    def test_cookie_when_there_is_no_bearer(self) -> None:
        account = user(Role.ANNOTATOR)
        auth = FakeAuth({"cookie-tok": account})
        assert get_current_user(request({"access_token": "cookie-tok"}), None, auth) is account  # type: ignore[arg-type]

    def test_bearer_wins_over_cookie(self) -> None:
        auth = FakeAuth({"bearer": user(Role.ADMIN)})
        get_current_user(request({"access_token": "cookie"}), "bearer", auth)  # type: ignore[arg-type]
        assert auth.seen == ["bearer"]

    def test_no_token(self) -> None:
        with pytest.raises(HTTPException) as error:
            get_current_user(request(), None, FakeAuth({}))  # type: ignore[arg-type]
        assert error.value.status_code == 401
        assert error.value.detail == "Not authenticated"
        assert error.value.headers == {"WWW-Authenticate": "Bearer"}

    def test_invalid_token(self) -> None:
        with pytest.raises(HTTPException) as error:
            get_current_user(request(), "bad", FakeAuth({}))  # type: ignore[arg-type]
        assert error.value.status_code == 401
        assert error.value.detail == "Could not validate credentials"


class TestRequireRole:
    @pytest.mark.parametrize("role", list(Role))
    def test_admin_only(self, role: Role) -> None:
        if role == Role.ADMIN:
            assert require_admin(user(role)).role == role
        else:
            with pytest.raises(HTTPException) as error:
                require_admin(user(role))
            assert error.value.status_code == 403

    @pytest.mark.parametrize(("role", "allowed"), [(Role.ADMIN, True), (Role.ORG_ADMIN, True), (Role.ANNOTATOR, False)])
    def test_managers(self, role: Role, allowed: bool) -> None:
        if allowed:
            require_manager(user(role))
        else:
            with pytest.raises(HTTPException):
                require_manager(user(role))

    def test_custom_roles(self) -> None:
        RequireRole(Role.ANNOTATOR)(user(Role.ANNOTATOR))
        with pytest.raises(HTTPException):
            RequireRole()(user(Role.ADMIN))


def uploader(role: Role, can_upload: bool | None) -> Any:
    """`can_upload=None` stands for an account without a profile."""
    account = user(role)
    account.profile = None if can_upload is None else SimpleNamespace(can_upload=can_upload)
    return account


class TestRequireUpload:
    @pytest.mark.parametrize("role", [Role.ADMIN, Role.ORG_ADMIN])
    def test_administrators_may_upload_without_the_flag(self, role: Role) -> None:
        account = uploader(role, False)
        assert require_upload(account) is account

    def test_annotator_with_the_flag_may_upload(self) -> None:
        account = uploader(Role.ANNOTATOR, True)
        assert require_upload(account) is account

    @pytest.mark.parametrize("can_upload", [False, None])
    def test_annotator_without_the_flag_may_not(self, can_upload: bool | None) -> None:
        with pytest.raises(HTTPException) as error:
            require_upload(uploader(Role.ANNOTATOR, can_upload))
        assert error.value.status_code == 403


class TestOrgScope:
    def test_system_admin_sees_every_organization(self) -> None:
        assert get_org_scope(user(Role.ADMIN, None)) is None

    @pytest.mark.parametrize("role", [Role.ORG_ADMIN, Role.ANNOTATOR])
    def test_members_see_their_organization(self, role: Role) -> None:
        assert get_org_scope(user(role, 7)) == 7

    def test_member_without_organization_is_refused(self) -> None:
        with pytest.raises(HTTPException) as error:
            get_org_scope(user(Role.ANNOTATOR, None))
        assert error.value.status_code == 403
