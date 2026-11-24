import os
from collections.abc import Generator
from uuid import uuid4

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.schema import CreateSchema, DropSchema

from app.database.base import Base
import app.models  # noqa: F401


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
