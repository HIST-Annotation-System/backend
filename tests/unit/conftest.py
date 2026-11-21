"""Shared fixtures for unit tests: an in-memory SQLite database, settings that don't read .env files, and
factories for the rows most services need."""

import uuid
from collections.abc import Iterator
from typing import Any

import bcrypt
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.config import Settings
from app.models import Account, Base, Hotspot, HotspotFolder, Organization, Role
from app.models.profile import AnnotatorProfile
from app.models.tag import Tag, TagDimension


def make_settings(**overrides: Any) -> Settings:
    values: dict[str, Any] = {
        "BUCKET_ENDPOINT": "http://storage",
        "BUCKET_ACCESS_KEY": "key",
        "BUCKET_SECRET_KEY": "secret",
        "BUCKET_NAME": "bucket",
        "BUCKET_REGION": "eu",
        "POSTGRES_USER": "u",
        "POSTGRES_PASSWORD": "p",
        "POSTGRES_DB": "db",
        "JWT_SECRET": "unit-test-secret",
        "JWT_ALGORITHM": "HS256",
        "ADMIN_USERNAME": "admin",
        "ADMIN_PASSWORD": "admin",
        **overrides,
    }
    return Settings(_env_file=None, **values)  # type: ignore[call-arg]


@pytest.fixture
def settings() -> Settings:
    return make_settings()


@pytest.fixture
def session_factory() -> Iterator[sessionmaker[Session]]:
    engine = create_engine("sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    yield sessionmaker(bind=engine, expire_on_commit=False)
    engine.dispose()


@pytest.fixture
def session(session_factory: sessionmaker[Session]) -> Iterator[Session]:
    with session_factory() as session:
        yield session


@pytest.fixture(autouse=True)
def fast_bcrypt(monkeypatch: pytest.MonkeyPatch) -> None:
    """The minimum cost factor: hashing stays real, but a test doesn't spend a quarter second per password."""
    real_gensalt = bcrypt.gensalt
    monkeypatch.setattr(bcrypt, "gensalt", lambda rounds=4, prefix=b"2b": real_gensalt(4, prefix))


class Factory:
    """Creates and flushes rows with sensible defaults."""

    def __init__(self, session: Session) -> None:
        self.session = session

    def organization(self, name: str | None = None) -> Organization:
        organization = Organization(name=name or f"Org {uuid.uuid4().hex[:6]}")
        self.session.add(organization)
        self.session.flush()
        return organization

    def account(
        self,
        role: Role = Role.ANNOTATOR,
        organization: Organization | None = None,
        username: str | None = None,
        with_profile: bool = True,
        **fields: Any,
    ) -> Account:
        account = Account(
            username=username or f"user-{uuid.uuid4().hex[:8]}",
            hashed_password=fields.pop("hashed_password", "x"),
            role=role,
            is_first_login=fields.pop("is_first_login", False),
            organization_id=organization.id if organization else None,
            **fields,
        )
        self.session.add(account)
        self.session.flush()
        if with_profile:
            self.session.add(AnnotatorProfile(account_id=account.id, first_name="F", last_name="L", can_upload=False))
            self.session.flush()
            self.session.refresh(account)
        return account

    def folder(self, organization: Organization, name: str | None = None) -> HotspotFolder:
        folder = HotspotFolder(name=name or f"Folder {uuid.uuid4().hex[:6]}", organization_id=organization.id)
        self.session.add(folder)
        self.session.flush()
        return folder

    def hotspot(self, folder: HotspotFolder, **fields: Any) -> Hotspot:
        values: dict[str, Any] = {"name": "H", "image_width": 100, "image_height": 100, **fields}
        hotspot = Hotspot(id=uuid.uuid4(), folder_id=folder.id, **values)
        self.session.add(hotspot)
        self.session.flush()
        return hotspot

    def tag(self, name: str, weight: int, dimension: TagDimension | None = None) -> Tag:
        tag = Tag(name=name, weight=weight, dimension=dimension)
        self.session.add(tag)
        self.session.flush()
        return tag


@pytest.fixture
def factory(session: Session) -> Factory:
    return Factory(session)


class MemoryStorage:
    """An in-memory stand-in for ObjectStorage that records what was written and deleted."""

    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}
        self.deleted_prefixes: list[str] = []
        self.multipart: dict[str, str] = {}
        self.aborted: list[str] = []
        self.completed: list[tuple[str, str, dict[int, str]]] = []

    def put_bytes(self, key: str, data: bytes, content_type: str) -> None:
        self.objects[key] = data

    def get_bytes(self, key: str) -> bytes:
        from app.exceptions.errors import StorageError

        if key not in self.objects:
            raise StorageError(f"missing {key}")
        return self.objects[key]

    def delete_prefix(self, prefix: str) -> None:
        self.deleted_prefixes.append(prefix)
        for key in [key for key in self.objects if key.startswith(prefix)]:
            del self.objects[key]

    def create_multipart_upload(self, key: str) -> str:
        upload_id = f"upload-{len(self.multipart) + 1}"
        self.multipart[upload_id] = key
        return upload_id

    def get_presigned_part_urls(self, key: str, upload_id: str, part_numbers: Any) -> dict[int, str]:
        return {number: f"https://s3/{key}?part={number}&upload={upload_id}" for number in part_numbers}

    def complete_multipart_upload(self, key: str, upload_id: str, etags: dict[int, str]) -> None:
        self.completed.append((key, upload_id, etags))

    def abort_multipart_uploads(self, key: str) -> None:
        self.aborted.append(key)


@pytest.fixture
def storage() -> MemoryStorage:
    return MemoryStorage()
