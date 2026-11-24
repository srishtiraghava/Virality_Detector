import shutil
import sqlite3
import subprocess
from collections.abc import AsyncGenerator, Generator
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.database.base import Base
from app.database.session import get_db
from app.main import app


def _create_test_video(output_path: Path, *, include_audio: bool) -> Path:
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg is None:
        pytest.fail("FFmpeg is required to generate test media")

    command = [
        ffmpeg,
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-f",
        "lavfi",
        "-i",
        "color=c=black:s=64x48:r=24:d=1",
    ]
    if include_audio:
        command.extend(
            [
                "-f",
                "lavfi",
                "-i",
                "sine=frequency=1000:sample_rate=44100:duration=1",
            ]
        )
    command.extend(
        [
            "-c:v",
            "mpeg4",
            "-pix_fmt",
            "yuv420p",
        ]
    )
    if include_audio:
        command.extend(["-c:a", "aac", "-shortest"])
    else:
        command.append("-an")
    command.extend(["-t", "1", "-f", "mp4", str(output_path)])

    result = subprocess.run(command, capture_output=True, check=False, text=True)
    if result.returncode != 0:
        pytest.fail("FFmpeg could not generate test media")
    return output_path


@pytest.fixture(scope="session")
def sample_video_file(tmp_path_factory: pytest.TempPathFactory) -> Path:
    output_path = tmp_path_factory.mktemp("test-media") / "sample.mp4"
    return _create_test_video(output_path, include_audio=True)


@pytest.fixture(scope="session")
def silent_video_file(tmp_path_factory: pytest.TempPathFactory) -> Path:
    output_path = tmp_path_factory.mktemp("test-media") / "silent.mp4"
    return _create_test_video(output_path, include_audio=False)


@pytest.fixture
def db_session() -> Generator[Session, None, None]:
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    @event.listens_for(engine, "connect")
    def enable_sqlite_foreign_keys(
        dbapi_connection: sqlite3.Connection,
        connection_record: object,
    ) -> None:
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    Base.metadata.create_all(engine)
    test_session_factory = sessionmaker(bind=engine, expire_on_commit=False)
    session = test_session_factory()
    try:
        yield session
    finally:
        session.close()
        Base.metadata.drop_all(engine)
        engine.dispose()


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture
async def client(
    anyio_backend: str,
    db_session: Session,
) -> AsyncGenerator[AsyncClient, None]:
    def override_get_db() -> Generator[Session, None, None]:
        yield db_session

    app.dependency_overrides[get_db] = override_get_db
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
    ) as test_client:
        try:
            yield test_client
        finally:
            app.dependency_overrides.pop(get_db, None)
