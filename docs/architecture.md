# Architecture

ViralScope AI starts as a modular monolith: one deployable backend with explicit module boundaries. This keeps local development and deployment straightforward while leaving room to separate workloads later if scale or ownership requires it.

## Repository layout

- `backend/app/api/v1/` — versioned health, authentication, video, and analysis-job HTTP routes and router composition.
- `backend/app/core/` — application configuration, logging, and cross-cutting concerns.
- `backend/app/database/` — SQLAlchemy declarative base, engine, session factory, and request dependency.
- `backend/app/models/` — SQLAlchemy persistence models for users, videos, and analysis jobs.
- `backend/app/schemas/` — Pydantic request, response, and job status contracts.
- `backend/app/repositories/` — SQLAlchemy persistence operations, including owner-scoped video access and analysis job lifecycle updates.
- `backend/app/services/upload/` — storage interface and local filesystem implementation, including staged file deletion.
- `backend/app/services/video/` — FFprobe metadata extraction, frame sampling, PySceneDetect scene detection, FFmpeg audio extraction, faster-whisper transcription, and the synchronous analysis pipeline.
- `backend/app/services/` — other future application services, grouped by domain (`audio`, `visual`, `transcript`, `features`, `prediction`, and `recommendations`).
- `backend/app/ml/` — future preprocessing, training, inference, evaluation, calibration, and explainability code; no ML implementation is included.
- `backend/app/workers/` — Celery application, task definitions, and per-task database session lifecycle.
- `backend/app/utils/` — small shared utilities that do not belong to a domain module.
- `backend/tests/` — unit and integration test suites. The initial API tests do not require external services.
- `backend/alembic/` — Alembic environment and revision scripts.
- `frontend/` — reserved for a React, TypeScript, and Vite client.
- `research/` — future dataset documentation, notebooks, experiments, and evaluation work.
- `ml_artifacts/` — generated model artifacts; binaries are excluded from Git.
- `docs/` — project and architecture documentation.

## Runtime boundaries

FastAPI routes live under `/api/v1`. Configuration is loaded centrally with `pydantic-settings`; secrets are not logged. The SQLAlchemy engine is created without opening a database connection, and connections are made only when a session or the database health check is used. PostgreSQL is the configured database. Redis at host port 6380 is the Celery broker and result backend for local development.

Video uploads require the existing bearer-token dependency. The upload service streams request data through a bounded temporary file, validates the container and video stream with FFprobe, then stores it through the storage interface. Current local storage uses generated storage keys and never derives filesystem paths from user filenames. Video and analysis-job queries are scoped to the authenticated owner. Analysis requests commit a queued database record before dispatching a Celery task. Workers open and close their own SQLAlchemy session, lock the job row while checking its state, and ignore duplicate deliveries after the job leaves queued. The analysis pipeline streams decoded frames through OpenCV at a configurable interval, applies configured output bounds, runs `ContentDetector` once, and uses FFmpeg to extract any available audio as bounded mono 16 kHz PCM WAV. faster-whisper loads lazily and caches one model per worker process; audio-bearing jobs produce timestamped transcript segments, while no-audio and no-speech results are represented distinctly. A v2 JSON summary, JPEG frames, and extracted audio are stored through the configured storage backend. Worker progress is committed at stage boundaries; the completed status is persisted only after applicable processing and artifact storage succeed. Dispatch and processing failures persist sanitized messages. VAD, sound classification, and ML stages are not implemented. Artifacts remain in storage, so no database migration is required.

Celery uses JSON-only messages and Redis databases 0 and 1 for broker and result storage by default. Windows local development uses the Celery solo pool. Celery/Redis results are operational metadata; PostgreSQL remains the source of truth for job status and error messages. No database migration is required for this worker milestone.

Audio extraction and transcription use `FFMPEG_BINARY`, `FFPROBE_BINARY`, `AUDIO_PROBE_TIMEOUT_SECONDS`, `AUDIO_EXTRACTION_TIMEOUT_SECONDS`, and `AUDIO_MAX_DURATION_SECONDS`. Transcription uses `WHISPER_MODEL_SIZE` (`base` by default), `WHISPER_DEVICE` (`cpu`), `WHISPER_COMPUTE_TYPE` (`int8`), `WHISPER_CPU_THREADS`, optional `WHISPER_LANGUAGE`, and optional `WHISPER_DOWNLOAD_ROOT`. Model weights are downloaded once when first needed if they are not already cached; subsequent jobs reuse the model within that worker process. No weights are downloaded by unit tests. Larger models and multiple worker processes increase memory use and CPU demand; the duration limit and subprocess timeouts bound audio work. VAD is deliberately not applied.

New jobs write `analysis/<video-id>/<job-id>/v2/metadata.json`, JPEGs under `frames/`, and (if present) `audio/audio.wav`. The artifact includes frames and scene boundaries, extraction/transcription status, audio metadata, detected language when available, transcript text and timed segments, and processing summaries. No raw frame/audio bytes or transcript columns are added to PostgreSQL. The schema reader still accepts legacy v1 visual artifacts.

## Future extension guidance

Add domain behavior to services, persistence operations to repositories, and wire new HTTP endpoints through the versioned API router. The initial `User`, `Video`, and `AnalysisJob` models use UUID identifiers and UTC timestamps. A video can retain multiple analysis-job records; creating another job does not overwrite prior job history. PostgreSQL integration tests require an explicit `TEST_DATABASE_URL` and use a temporary isolated schema, while unit tests use SQLite in memory. Keep ML dependencies and generated artifacts out of the web request path unless a later, deliberate deployment design requires otherwise.
