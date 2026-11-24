from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, EmailStr, Field, SecretStr, field_validator


class UserCreate(BaseModel):
    email: EmailStr
    password: SecretStr = Field(min_length=8, max_length=128)
    full_name: str | None = Field(default=None, max_length=255)

    @field_validator("email", mode="before")
    @classmethod
    def normalize_email(cls, value: object) -> object:
        if isinstance(value, str):
            return value.strip().casefold()
        return value


class UserLogin(BaseModel):
    email: EmailStr
    password: SecretStr = Field(min_length=1, max_length=128)

    @field_validator("email", mode="before")
    @classmethod
    def normalize_email(cls, value: object) -> object:
        if isinstance(value, str):
            return value.strip().casefold()
        return value


class UserResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    email: EmailStr
    full_name: str | None
    is_active: bool
    created_at: datetime
    updated_at: datetime
