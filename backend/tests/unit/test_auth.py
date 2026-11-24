from datetime import UTC, datetime, timedelta
from uuid import uuid4

import httpx
import jwt
import pytest
from pydantic import SecretStr
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.v1.endpoints import auth as auth_endpoints
from app.core.config import settings
from app.core.security import (
    JWT_ALGORITHM,
    create_access_token,
    decode_access_token,
    hash_password,
    verify_password,
)
from app.models.user import User
from app.repositories.users import create_user, get_user_by_email
from app.schemas.user import UserCreate

_TEST_SECRET = "test-jwt-signing-key-that-is-at-least-32-bytes"


@pytest.fixture(autouse=True)
def configure_test_jwt(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "secret_key", SecretStr(_TEST_SECRET))
    monkeypatch.setattr(settings, "access_token_expire_minutes", 30)


@pytest.mark.anyio
async def test_register_creates_argon2id_hash_and_safe_response(
    client: httpx.AsyncClient,
    db_session: Session,
) -> None:
    response = await client.post(
        "/api/v1/auth/register",
        json={
            "email": "  New.User@Example.com ",
            "password": "correct-horse-battery",
            "full_name": "New User",
        },
    )

    assert response.status_code == 201
    body = response.json()
    assert body["email"] == "new.user@example.com"
    assert body["full_name"] == "New User"
    assert "hashed_password" not in body
    assert "password" not in body

    user = get_user_by_email(db_session, "new.user@example.com")
    assert user is not None
    assert user.hashed_password != "correct-horse-battery"
    assert user.hashed_password.startswith("$argon2id$")
    assert verify_password("correct-horse-battery", user.hashed_password)


@pytest.mark.anyio
async def test_registration_rejects_duplicate_email_case_insensitively(
    client: httpx.AsyncClient,
) -> None:
    payload = {
        "email": "duplicate@example.com",
        "password": "correct-horse-battery",
    }
    first = await client.post("/api/v1/auth/register", json=payload)
    duplicate = await client.post(
        "/api/v1/auth/register",
        json={**payload, "email": "DUPLICATE@example.com"},
    )

    assert first.status_code == 201
    assert duplicate.status_code == 409


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("payload", "field"),
    [
        (
            {"email": "not-an-email", "password": "correct-horse-battery"},
            "email",
        ),
        (
            {"email": "valid@example.com", "password": "short"},
            "password",
        ),
    ],
)
async def test_registration_validates_email_and_password(
    client: httpx.AsyncClient,
    payload: dict[str, str],
    field: str,
) -> None:
    response = await client.post("/api/v1/auth/register", json=payload)

    assert response.status_code == 422
    errors = response.json()["detail"]
    assert any(error["loc"][-1] == field for error in errors)
    assert all("input" not in error for error in errors)


@pytest.mark.anyio
async def test_login_returns_bearer_token_for_valid_credentials(
    client: httpx.AsyncClient,
) -> None:
    await client.post(
        "/api/v1/auth/register",
        json={
            "email": "login@example.com",
            "password": "correct-horse-battery",
        },
    )

    response = await client.post(
        "/api/v1/auth/login",
        json={"email": " LOGIN@example.com ", "password": "correct-horse-battery"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["token_type"] == "bearer"
    assert body["expires_in"] == 1800
    assert decode_access_token(body["access_token"])
    assert "password" not in body
    assert "hashed_password" not in body


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("email", "password"),
    [
        ("login-failure@example.com", "wrong-password"),
        ("unknown@example.com", "correct-horse-battery"),
    ],
)
async def test_login_rejects_invalid_credentials_generically(
    client: httpx.AsyncClient,
    email: str,
    password: str,
) -> None:
    await client.post(
        "/api/v1/auth/register",
        json={
            "email": "login-failure@example.com",
            "password": "correct-horse-battery",
        },
    )

    response = await client.post(
        "/api/v1/auth/login",
        json={"email": email, "password": password},
    )

    assert response.status_code == 401
    assert response.json()["detail"] == "Invalid email or password"
    assert response.headers["www-authenticate"] == "Bearer"


@pytest.mark.anyio
async def test_inactive_user_cannot_login(
    client: httpx.AsyncClient,
    db_session: Session,
) -> None:
    await client.post(
        "/api/v1/auth/register",
        json={
            "email": "inactive@example.com",
            "password": "correct-horse-battery",
        },
    )
    user = get_user_by_email(db_session, "inactive@example.com")
    assert user is not None
    user.is_active = False
    db_session.commit()

    response = await client.post(
        "/api/v1/auth/login",
        json={
            "email": "inactive@example.com",
            "password": "correct-horse-battery",
        },
    )

    assert response.status_code == 401
    assert response.json()["detail"] == "Invalid email or password"


def test_password_hashing_uses_argon2id_and_rejects_wrong_password() -> None:
    hashed_password = hash_password("correct-horse-battery")

    assert hashed_password.startswith("$argon2id$")
    assert verify_password("correct-horse-battery", hashed_password)
    assert not verify_password("wrong-password", hashed_password)
    assert not verify_password("wrong-password", None)
    assert not verify_password("wrong-password", "malformed-hash")
    assert not verify_password(
        "wrong-password",
        hashed_password[:-3] + "xxx",
    )


def test_jwt_round_trip_and_rejects_expired_malformed_and_wrong_signature() -> None:
    subject = uuid4()
    token = create_access_token(subject)
    assert decode_access_token(token) == subject

    now = datetime.now(UTC)
    expired_token = jwt.encode(
        {"sub": str(subject), "iat": now - timedelta(minutes=2), "exp": now - timedelta(minutes=1)},
        _TEST_SECRET,
        algorithm=JWT_ALGORITHM,
    )
    invalid_signature = jwt.encode(
        {
            "sub": str(subject),
            "iat": now,
            "exp": now + timedelta(minutes=1),
        },
        "a-different-signing-key-that-is-at-least-32-bytes",
        algorithm=JWT_ALGORITHM,
    )

    for invalid_token in ("not-a-jwt", expired_token, invalid_signature):
        with pytest.raises(ValueError, match="Invalid access token"):
            decode_access_token(invalid_token)


@pytest.mark.anyio
async def test_me_requires_valid_token_and_returns_safe_user(
    client: httpx.AsyncClient,
) -> None:
    registration = await client.post(
        "/api/v1/auth/register",
        json={
            "email": "me@example.com",
            "password": "correct-horse-battery",
            "full_name": "Current User",
        },
    )
    assert registration.status_code == 201
    login = await client.post(
        "/api/v1/auth/login",
        json={"email": "me@example.com", "password": "correct-horse-battery"},
    )
    token = login.json()["access_token"]

    response = await client.get(
        "/api/v1/auth/me",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 200
    assert response.json()["email"] == "me@example.com"
    assert response.json()["full_name"] == "Current User"
    assert "hashed_password" not in response.json()


@pytest.mark.anyio
async def test_me_rejects_missing_invalid_and_nonexistent_user_tokens(
    client: httpx.AsyncClient,
) -> None:
    missing = await client.get("/api/v1/auth/me")
    invalid = await client.get(
        "/api/v1/auth/me",
        headers={"Authorization": "Bearer malformed-token"},
    )
    nonexistent = await client.get(
        "/api/v1/auth/me",
        headers={"Authorization": f"Bearer {create_access_token(uuid4())}"},
    )

    assert missing.status_code == 401
    assert invalid.status_code == 401
    assert nonexistent.status_code == 401
    assert all(
        response.headers["www-authenticate"] == "Bearer"
        for response in (missing, invalid, nonexistent)
    )


@pytest.mark.anyio
async def test_me_rejects_inactive_user_token(
    client: httpx.AsyncClient,
    db_session: Session,
) -> None:
    registration = await client.post(
        "/api/v1/auth/register",
        json={
            "email": "inactive-me@example.com",
            "password": "correct-horse-battery",
        },
    )
    user = get_user_by_email(db_session, "inactive-me@example.com")
    assert user is not None
    token = create_access_token(user.id)
    user.is_active = False
    db_session.commit()

    response = await client.get(
        "/api/v1/auth/me",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert registration.status_code == 201
    assert response.status_code == 401


@pytest.mark.anyio
async def test_registration_maps_duplicate_integrity_error_to_conflict(
    client: httpx.AsyncClient,
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    existing_user = create_user(
        db_session,
        UserCreate(
            email="race@example.com",
            password="correct-horse-battery",
        ),
        hashed_password=hash_password("correct-horse-battery"),
    )

    lookups = iter((None, existing_user))
    monkeypatch.setattr(
        auth_endpoints,
        "get_user_by_email",
        lambda session, email: next(lookups),
    )

    def raise_duplicate_integrity_error(*args, **kwargs) -> None:
        raise IntegrityError("INSERT", {}, Exception("unique constraint"))

    monkeypatch.setattr(auth_endpoints, "create_user", raise_duplicate_integrity_error)
    response = await client.post(
        "/api/v1/auth/register",
        json={
            "email": "race@example.com",
            "password": "correct-horse-battery",
        },
    )

    assert response.status_code == 409
