from pathlib import Path

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
    monkeypatch.setenv("CELERY_BROKER_URL", "redis://127.0.0.1:6380/4")
    monkeypatch.setenv("CELERY_RESULT_BACKEND", "redis://127.0.0.1:6380/5")
    monkeypatch.setenv("CORS_ORIGINS", "http://localhost:5173,https://example.test")
    monkeypatch.setenv("MAX_UPLOAD_SIZE_MB", "42")
    monkeypatch.setenv("STORAGE_BACKEND", "test-storage")
    monkeypatch.setenv("STORAGE_LOCAL_PATH", "test-storage")
    monkeypatch.setenv("FRAME_SAMPLING_INTERVAL_SECONDS", "2.5")
    monkeypatch.setenv("FRAME_MAX_COUNT", "12")
    monkeypatch.setenv("FRAME_MAX_WIDTH", "640")
    monkeypatch.setenv("FRAME_MAX_HEIGHT", "360")
    monkeypatch.setenv("SCENE_DETECTION_THRESHOLD", "18.5")
    monkeypatch.setenv("SCENE_DETECTION_MIN_SCENE_LEN_FRAMES", "8")
    monkeypatch.setenv("FFMPEG_BINARY", "ffmpeg-test")
    monkeypatch.setenv("FFPROBE_BINARY", "ffprobe-test")
    monkeypatch.setenv("AUDIO_PROBE_TIMEOUT_SECONDS", "12")
    monkeypatch.setenv("AUDIO_EXTRACTION_TIMEOUT_SECONDS", "90")
    monkeypatch.setenv("AUDIO_MAX_DURATION_SECONDS", "420")
    monkeypatch.setenv("WHISPER_MODEL_SIZE", "small")
    monkeypatch.setenv("WHISPER_DEVICE", "cuda")
    monkeypatch.setenv("WHISPER_COMPUTE_TYPE", "float16")
    monkeypatch.setenv("WHISPER_LANGUAGE", "en")
    monkeypatch.setenv("WHISPER_CPU_THREADS", "6")
    monkeypatch.setenv("WHISPER_DOWNLOAD_ROOT", "test-model-cache")
    monkeypatch.setenv("VAD_THRESHOLD", "0.65")
    monkeypatch.setenv("VAD_MIN_SPEECH_DURATION_MS", "75")
    monkeypatch.setenv("VAD_MIN_SILENCE_DURATION_MS", "140")
    monkeypatch.setenv("VAD_SPEECH_PAD_MS", "25")

    config = Settings(_env_file=None)

    assert config.app_name == "Test ViralScope"
    assert config.app_env == "test"
    assert config.debug is True
    assert config.database_url.endswith("/test_db")
    assert config.secret_key == SecretStr("test-only-secret")
    assert config.access_token_expire_minutes == 45
    assert config.celery_broker_url == "redis://127.0.0.1:6380/4"
    assert config.celery_result_backend == "redis://127.0.0.1:6380/5"
    assert config.cors_origins == [
        "http://localhost:5173",
        "https://example.test",
    ]
    assert config.max_upload_size_mb == 42
    assert config.storage_backend == "test-storage"
    assert str(config.storage_local_path) == "test-storage"
    assert config.frame_sampling_interval_seconds == 2.5
    assert config.frame_max_count == 12
    assert config.frame_max_width == 640
    assert config.frame_max_height == 360
    assert config.scene_detection_threshold == 18.5
    assert config.scene_detection_min_scene_len_frames == 8
    assert config.ffmpeg_binary == "ffmpeg-test"
    assert config.ffprobe_binary == "ffprobe-test"
    assert config.audio_probe_timeout_seconds == 12
    assert config.audio_extraction_timeout_seconds == 90
    assert config.audio_max_duration_seconds == 420
    assert config.whisper_model_size == "small"
    assert config.whisper_device == "cuda"
    assert config.whisper_compute_type == "float16"
    assert config.whisper_language == "en"
    assert config.whisper_cpu_threads == 6
    assert config.whisper_download_root == Path("test-model-cache")
    assert config.vad_threshold == 0.65
    assert config.vad_min_speech_duration_ms == 75
    assert config.vad_min_silence_duration_ms == 140
    assert config.vad_speech_pad_ms == 25


def test_settings_default_celery_urls_are_local_redis() -> None:
    config = Settings(_env_file=None)

    assert config.celery_broker_url == "redis://127.0.0.1:6380/0"
    assert config.celery_result_backend == "redis://127.0.0.1:6380/1"
