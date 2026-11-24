import os
from collections.abc import AsyncGenerator, Generator
from pathlib import Path
from uuid import uuid4

import httpx
import pytest
from pydantic import SecretStr
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.schema import CreateSchema, DropSchema

from app.core.config import settings
from app.database.base import Base
from app.database.session import get_db
from app.main import app as fastapi_app
import app.models  # noqa: F401


@pytest.fixture
async def video_integration_client(
    integration_session: Session,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> AsyncGenerator[httpx.AsyncClient, None]:
    monkeypatch.setattr(
        settings,
        "secret_key",
        SecretStr("video-integration-jwt-key-with-32-bytes"),
    )
    monkeypatch.setattr(settings, "storage_local_path", tmp_path / "storage")

    def override_get_db() -> Generator[Session, None, None]:
        yield integration_session

    fastapi_app.dependency_overrides[get_db] = override_get_db
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=fastapi_app),
            base_url="http://testserver",
        ) as client:
            yield client
    finally:
        fastapi_app.dependency_overrides.pop(get_db, None)


@pytest.fixture
def integration_session() -> Generator[Session, None, None]:
    test_database_url = os.environ.get("TEST_DATABASE_URL")
    if not test_database_url:
        pytest.skip("Set TEST_DATABASE_URL to run PostgreSQL integration tests")

    engine = create_engine(test_database_url, pool_pre_ping=True)
    schema_name = f"viralscope_test_{uuid4().hex}"
    schema_created = False
    connection = None
    session = None

    try:
        with engine.begin() as setup_connection:
            setup_connection.execute(CreateSchema(schema_name))
        schema_created = True

        connection = engine.connect().execution_options(
            schema_translate_map={None: schema_name}
        )
        Base.metadata.create_all(connection)
        session = Session(bind=connection, expire_on_commit=False)
        yield session
    finally:
        if session is not None:
            session.close()
        if connection is not None:
            connection.close()
        if schema_created:
            with engine.begin() as cleanup_connection:
                cleanup_connection.execute(DropSchema(schema_name, cascade=True))
        engine.dispose()
