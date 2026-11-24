from collections.abc import AsyncGenerator, Generator

import httpx
import pytest
from pydantic import SecretStr
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.security import decode_access_token, verify_password
from app.database.session import get_db
from app.main import app
from app.models.user import User

_TEST_SECRET = "integration-jwt-signing-key-with-at-least-32-bytes"


@pytest.fixture
async def auth_integration_client(
    integration_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> AsyncGenerator[httpx.AsyncClient, None]:
    monkeypatch.setattr(settings, "secret_key", SecretStr(_TEST_SECRET))
    monkeypatch.setattr(settings, "access_token_expire_minutes", 30)

    def override_get_db() -> Generator[Session, None, None]:
        yield integration_session

    app.dependency_overrides[get_db] = override_get_db
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://testserver",
        ) as client:
            yield client
    finally:
        app.dependency_overrides.pop(get_db, None)


@pytest.mark.anyio
async def test_postgresql_authentication_flow_persists_user_and_authenticates(
    auth_integration_client: httpx.AsyncClient,
    integration_session: Session,
) -> None:
    registration = await auth_integration_client.post(
        "/api/v1/auth/register",
        json={
            "email": "  Integration.User@Example.com ",
            "password": "integration-password",
            "full_name": "Integration User",
        },
    )

    assert registration.status_code == 201
    registration_body = registration.json()
    assert registration_body["email"] == "integration.user@example.com"
    assert "hashed_password" not in registration_body
    assert "password" not in registration_body

    user = integration_session.scalar(
        select(User).where(User.email == "integration.user@example.com")
    )
    assert user is not None
    assert user.hashed_password != "integration-password"
    assert user.hashed_password.startswith("$argon2id$")
    assert verify_password("integration-password", user.hashed_password)

    login = await auth_integration_client.post(
        "/api/v1/auth/login",
        json={
            "email": "INTEGRATION.USER@example.com",
            "password": "integration-password",
        },
    )
    assert login.status_code == 200
    token_body = login.json()
    assert token_body["token_type"] == "bearer"
    assert token_body["expires_in"] == 1800
    assert decode_access_token(token_body["access_token"]) == user.id

    current_user = await auth_integration_client.get(
        "/api/v1/auth/me",
        headers={"Authorization": f"Bearer {token_body['access_token']}"},
    )
    assert current_user.status_code == 200
    current_user_body = current_user.json()
    assert current_user_body["id"] == str(user.id)
    assert current_user_body["email"] == "integration.user@example.com"
    assert "hashed_password" not in current_user_body


@pytest.mark.anyio
async def test_postgresql_registration_rejects_duplicate_normalized_email(
    auth_integration_client: httpx.AsyncClient,
) -> None:
    payload = {
        "email": "duplicate.integration@example.com",
        "password": "integration-password",
    }

    first = await auth_integration_client.post(
        "/api/v1/auth/register",
        json=payload,
    )
    duplicate = await auth_integration_client.post(
        "/api/v1/auth/register",
        json={
            **payload,
            "email": "  DUPLICATE.INTEGRATION@example.com ",
        },
    )

    assert first.status_code == 201
    assert duplicate.status_code == 409
