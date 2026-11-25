# ViralScope AI

ViralScope AI is a modular-monolith foundation for a multimodal video intelligence and virality-prediction platform. The backend includes PostgreSQL persistence, email/password authentication, authenticated video upload and management, FFprobe metadata extraction, and analysis-job creation. Analysis execution, machine learning, and recommendations are not implemented.

## Architecture

- **Backend:** Python 3.11, FastAPI, Pydantic v2, and SQLAlchemy 2.0.
- **Persistence:** PostgreSQL, configured through environment variables and managed with Alembic. The initial user, video, and analysis-job migration is applied.
- **Local infrastructure:** Docker Compose provides PostgreSQL and Redis. Celery workers use Redis for task transport and result storage.
- **Frontend:** `frontend/` is reserved for a future React, TypeScript, and Vite application.
- **Research and artifacts:** `research/` and `ml_artifacts/` keep future experiments and generated model files separate from application code.

The backend separates HTTP routes, configuration, database access, models, schemas, repositories, and domain services. Upload storage and FFprobe metadata extraction are implemented; ML subpackages remain structural only.

## Prerequisites

- Windows 10/11 with PowerShell.
- Python 3.11.
- Git.
- Docker Desktop with the Docker Engine running for PostgreSQL and Redis.
- FFmpeg tools on `PATH`: FFprobe is required by the API; FFmpeg is used to generate valid media fixtures for tests.

## Windows PowerShell setup

Run these commands from the repository root:

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r backend\requirements-dev.txt
Copy-Item .env.example .env
```

Edit `.env` before starting local infrastructure. Replace the placeholder username and password, and set `DATABASE_URL` to use the same credentials and database, for example:

```text
DATABASE_URL=postgresql+psycopg://<your-user>:<your-password>@127.0.0.1:5433/viralscope
```

Use URL-encoded characters if the database password contains reserved URL characters. Set `SECRET_KEY` to a securely generated value of at least 32 bytes; it signs access tokens. `ACCESS_TOKEN_EXPIRE_MINUTES` controls the access-token lifetime (default: 30 minutes).

If PowerShell blocks activation, allow scripts for the current process and activate again:

```powershell
Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope Process
.\.venv\Scripts\Activate.ps1
```

## Run the API

From the repository root with the virtual environment active:

```powershell
python -m uvicorn app.main:app --app-dir backend --reload
```

The API is available at `http://127.0.0.1:8000`. Interactive API documentation is at `http://127.0.0.1:8000/docs`.

- `GET /` returns basic application information.
- `GET /api/v1/health` reports application status, version, and environment without requiring a database connection.
- `GET /api/v1/health/database` checks PostgreSQL and returns HTTP 503 when it is unavailable.
- `POST /api/v1/auth/register` creates an account.
- `POST /api/v1/auth/login` verifies credentials and returns a bearer access token.
- `GET /api/v1/auth/me` returns the authenticated user's safe profile.
- `POST /api/v1/videos/upload` validates and stores an authenticated user's video.
- `GET /api/v1/videos` and `GET /api/v1/videos/{video_id}` list or retrieve only the authenticated user's videos.
- `DELETE /api/v1/videos/{video_id}` removes an owned video and its local media file.
- `POST /api/v1/videos/{video_id}/analyze` persists a queued analysis job and dispatches it to Celery; it does not run analysis in the API process.
- `GET /api/v1/jobs/{job_id}` retrieves a job only when it belongs to the authenticated user.

Uploads accept MP4, MOV, M4V, WebM, and MKV containers with matching MIME types. FFprobe verifies the actual container and video stream before a record is marked available. The maximum upload size is controlled by `MAX_UPLOAD_SIZE_MB`; local files are stored under `STORAGE_LOCAL_PATH`.

Example PowerShell upload using the bearer token from the authentication example:

```powershell
curl.exe -X POST http://127.0.0.1:8000/api/v1/videos/upload `
    -H "Authorization: Bearer $token" `
    -F "file=@C:\path\to\video.mp4;type=video/mp4" `
    -F "target_platform=shorts" `
    -F "content_category=education"
```

Example PowerShell authentication flow:

```powershell
$register = @{
    email = "you@example.com"
    password = "<your-password>"
    full_name = "Your Name"
} | ConvertTo-Json
Invoke-RestMethod -Method Post -Uri http://127.0.0.1:8000/api/v1/auth/register `
    -ContentType "application/json" -Body $register

$login = @{
    email = "you@example.com"
    password = "<your-password>"
} | ConvertTo-Json
$token = (Invoke-RestMethod -Method Post -Uri http://127.0.0.1:8000/api/v1/auth/login `
    -ContentType "application/json" -Body $login).access_token
Invoke-RestMethod -Uri http://127.0.0.1:8000/api/v1/auth/me `
    -Headers @{ Authorization = "Bearer $token" }
```

## PostgreSQL and Redis with Docker Compose

With Docker Desktop running and `.env` configured:

```powershell
docker compose up -d postgres redis
docker compose ps
docker compose down
```

PostgreSQL data persists in the `postgres_data` named volume. Compose health checks report when PostgreSQL and Redis are ready. Redis is published on host port 6380; the API and worker run separately from Compose.

## Background analysis jobs

The API persists each analysis job as `queued` before publishing its Celery task. If dispatch fails, it persists a sanitized `failed` status and returns HTTP 503 with the job ID so the failure is visible. Workers claim queued jobs under a PostgreSQL row lock; duplicate deliveries of a non-queued job are ignored. Worker failures are rolled back and each task owns and closes its database session.

The actual multimodal pipeline is deliberately not implemented. A worker validates that the job, owner, video, and local media exist, transitions the job through `processing`, then records `failed` with the explicit message that the pipeline is not implemented. It never fabricates analysis output or marks a job completed.

The defaults use the existing Redis port: broker `redis://127.0.0.1:6380/0`, results `redis://127.0.0.1:6380/1`. Override these through `CELERY_BROKER_URL` and `CELERY_RESULT_BACKEND` for other environments.

Start Redis using the existing Compose service, without changing ports:

```powershell
docker compose up -d redis
docker compose ps redis
```

In another PowerShell window, start FastAPI as shown above. Start a Windows development worker using Celery's solo pool:

```powershell
Push-Location backend
python -m celery -A app.workers.celery_app:celery_app worker --pool=solo --loglevel=INFO
```

In another terminal, from `backend\`, publish and inspect an operational worker check:

```powershell
$taskId = python -m celery -A app.workers.celery_app:celery_app call app.workers.tasks.healthcheck
python -m celery -A app.workers.celery_app:celery_app result $taskId
```

The `healthcheck` task verifies queue delivery only. Task state and failure messages for analysis jobs are persisted in PostgreSQL; Celery's Redis result backend is for task execution results, not the source of truth for job state.

To delete the local database volume as well as stop the services, run `docker compose down --volumes`; this permanently deletes the local database data.

## Run tests

From the repository root (the commands run pytest with `backend\` as its working directory so the `app` package resolves directly):

```powershell
Push-Location backend
python -m pytest tests
Pop-Location
```

Unit tests use FastAPI's in-process client and SQLite. PostgreSQL integration tests require an explicit `TEST_DATABASE_URL` and use a temporary isolated schema.

## Database migrations

The initial migration creates `users`, `videos`, and `analysis_jobs` and is applied at revision `009dcf5dec6b`. For future revisions, inspect the generated migration before applying it with `python -m alembic upgrade head` from `backend\`.

Video media metadata adds nullable `video_codec` and `has_audio` columns. Revision `f3f4f6417ac5` is generated for review and has not been applied. Do not apply it until the migration is approved.

PostgreSQL integration tests require an explicit `TEST_DATABASE_URL` and create a unique temporary schema that is dropped after each test. Unit tests use in-memory SQLite and do not require Docker.
