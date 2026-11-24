# ViralScope AI

ViralScope AI is a modular-monolith foundation for a multimodal video intelligence and virality-prediction platform. The backend includes PostgreSQL persistence foundations and email/password authentication with Argon2id password hashing and signed JWT access tokens. Video processing, machine learning, and product features are not implemented.

## Architecture

- **Backend:** Python 3.11, FastAPI, Pydantic v2, and SQLAlchemy 2.0.
- **Persistence:** PostgreSQL, configured through environment variables and managed with Alembic. The initial user, video, and analysis-job migration is applied.
- **Local infrastructure:** Docker Compose provides PostgreSQL and Redis. Redis is available for future background processing; no worker is included.
- **Frontend:** `frontend/` is reserved for a future React, TypeScript, and Vite application.
- **Research and artifacts:** `research/` and `ml_artifacts/` keep future experiments and generated model files separate from application code.

The backend separates HTTP routes, configuration, database access, models, schemas, repositories, services, and ML concerns. Service and ML subpackages are structural only.

## Prerequisites

- Windows 10/11 with PowerShell.
- Python 3.11.
- Git.
- Docker Desktop with the Docker Engine running for PostgreSQL and Redis.

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

PostgreSQL data persists in the `postgres_data` named volume. Compose health checks report when PostgreSQL and Redis are ready. The API is run separately above and is not included as a Compose service.

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

PostgreSQL integration tests require an explicit `TEST_DATABASE_URL` and create a unique temporary schema that is dropped after each test. Unit tests use in-memory SQLite and do not require Docker.
