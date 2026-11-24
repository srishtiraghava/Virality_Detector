import pytest
from pydantic import SecretStr

from app.core.config import Settings


def test_settings_load_from_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APP_NAME", "Test ViralScope")
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("DEBUG", "true")
    monkeypatch.setenv(
        "DATABASE_URL",
        "postgresql+psycopg://test-user:test-password@localhost:5432/test_db",
    )
    monkeypatch.setenv("SECRET_KEY", "test-only-secret")
    monkeypatch.setenv("ACCESS_TOKEN_EXPIRE_MINUTES", "45")
    monkeypatch.setenv("CORS_ORIGINS", "http://localhost:5173,https://example.test")
    monkeypatch.setenv("MAX_UPLOAD_SIZE_MB", "42")
    monkeypatch.setenv("STORAGE_BACKEND", "test-storage")
    monkeypatch.setenv("STORAGE_LOCAL_PATH", "test-storage")

    config = Settings(_env_file=None)

    assert config.app_name == "Test ViralScope"
    assert config.app_env == "test"
    assert config.debug is True
    assert config.database_url.endswith("/test_db")
    assert config.secret_key == SecretStr("test-only-secret")
    assert config.access_token_expire_minutes == 45
    assert config.cors_origins == [
        "http://localhost:5173",
        "https://example.test",
    ]
    assert config.max_upload_size_mb == 42
    assert config.storage_backend == "test-storage"
    assert str(config.storage_local_path) == "test-storage"
