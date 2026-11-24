from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.dependencies import get_current_user
from app.core.config import settings
from app.core.security import create_access_token, hash_password, verify_password
from app.database.session import get_db
from app.models.user import User
from app.repositories.users import create_user, get_user_by_email
from app.schemas.auth import AccessTokenResponse
from app.schemas.user import UserCreate, UserLogin, UserResponse

router = APIRouter()
DatabaseSession = Annotated[Session, Depends(get_db)]


@router.post(
    "/register",
    response_model=UserResponse,
    status_code=status.HTTP_201_CREATED,
)
def register(user_data: UserCreate, session: DatabaseSession) -> User:
    email = str(user_data.email)
    if get_user_by_email(session, email) is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="An account with this email already exists",
        )

    hashed_password = hash_password(user_data.password.get_secret_value())
    try:
        user = create_user(session, user_data, hashed_password=hashed_password)
    except IntegrityError:
        session.rollback()
        if get_user_by_email(session, email) is not None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="An account with this email already exists",
            ) from None
        raise
    return user


@router.post("/login", response_model=AccessTokenResponse)
def login(credentials: UserLogin, session: DatabaseSession) -> AccessTokenResponse:
    user = get_user_by_email(session, str(credentials.email))
    password = credentials.password.get_secret_value()
    password_is_valid = verify_password(
        password,
        user.hashed_password if user is not None else None,
    )
    if user is None or not password_is_valid:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email or password",
            headers={"WWW-Authenticate": "Bearer"},
        )
    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email or password",
            headers={"WWW-Authenticate": "Bearer"},
        )

    return AccessTokenResponse(
        access_token=create_access_token(user.id),
        expires_in=settings.access_token_expire_minutes * 60,
    )


@router.get("/me", response_model=UserResponse)
def read_current_user(
    current_user: Annotated[User, Depends(get_current_user)],
) -> User:
    return current_user
