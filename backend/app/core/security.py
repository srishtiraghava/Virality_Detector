from datetime import UTC, datetime, timedelta
from secrets import token_urlsafe
from uuid import UUID

import jwt
from pwdlib import PasswordHash
from pwdlib.exceptions import HashVerificationError, UnknownHashError
from pwdlib.hashers.argon2 import Argon2Hasher

from app.core.config import settings

JWT_ALGORITHM = "HS256"
_password_hash = PasswordHash((Argon2Hasher(),))
_DUMMY_PASSWORD_HASH = _password_hash.hash(token_urlsafe(32))


def hash_password(password: str) -> str:
    return _password_hash.hash(password)


def verify_password(password: str, hashed_password: str | None) -> bool:
    if hashed_password is None:
        hashed_password = _DUMMY_PASSWORD_HASH
    try:
        return _password_hash.verify(password, hashed_password)
    except (HashVerificationError, UnknownHashError, ValueError):
        return False


def _jwt_signing_key() -> str:
    key = settings.secret_key.get_secret_value()
    if len(key.encode("utf-8")) < 32:
        raise RuntimeError("SECRET_KEY must contain at least 32 bytes")
    return key


def create_access_token(
    subject: UUID,
    *,
    expires_delta: timedelta | None = None,
) -> str:
    now = datetime.now(UTC)
    expires_at = now + (
        expires_delta
        if expires_delta is not None
        else timedelta(minutes=settings.access_token_expire_minutes)
    )
    claims = {
        "sub": str(subject),
        "iat": now,
        "exp": expires_at,
    }
    return jwt.encode(claims, _jwt_signing_key(), algorithm=JWT_ALGORITHM)


def decode_access_token(token: str) -> UUID:
    try:
        claims = jwt.decode(
            token,
            _jwt_signing_key(),
            algorithms=[JWT_ALGORITHM],
            options={"require": ["exp", "iat", "sub"]},
        )
        return UUID(claims["sub"])
    except (jwt.InvalidTokenError, KeyError, TypeError, ValueError):
        raise ValueError("Invalid access token") from None
