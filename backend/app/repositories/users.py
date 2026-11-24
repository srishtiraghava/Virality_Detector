from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.user import User
from app.schemas.user import UserCreate


def create_user(session: Session, user_data: UserCreate, *, hashed_password: str) -> User:
    if not hashed_password:
        raise ValueError("hashed_password must not be empty")

    user = User(
        email=str(user_data.email),
        hashed_password=hashed_password,
        full_name=user_data.full_name,
    )
    session.add(user)
    session.commit()
    session.refresh(user)
    return user


def get_user_by_id(session: Session, user_id: UUID) -> User | None:
    return session.get(User, user_id)


def get_user_by_email(session: Session, email: str) -> User | None:
    statement = select(User).where(User.email == email)
    return session.scalar(statement)
