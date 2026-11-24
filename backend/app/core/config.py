from pathlib import Path
from typing import Annotated

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parents[3]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=PROJECT_ROOT / ".env",
        env_file_encoding="utf-8",
        env_ignore_empty=True,
        env_parse_delimiter=",",
        case_sensitive=False,
        extra="ignore",
    )

    app_name: str = "ViralScope AI"
    app_env: str = "development"
    debug: bool = False
    database_url: str = "postgresql+psycopg://localhost/viralscope"
    secret_key: SecretStr = Field(default=SecretStr(""), repr=False)
    access_token_expire_minutes: int = Field(default=30, gt=0)
    cors_origins: Annotated[list[str], NoDecode] = Field(
        default_factory=lambda: ["http://localhost:5173"]
    )
    max_upload_size_mb: int = Field(default=500, gt=0)
    storage_backend: str = "local"
    storage_local_path: Path = Path("storage")

    @field_validator("cors_origins", mode="before")
    @classmethod
    def split_cors_origins(cls, value: str | list[str]) -> str | list[str]:
        if isinstance(value, str):
            return [origin.strip() for origin in value.split(",") if origin.strip()]
        return value


settings = Settings()
