# ViralScope AI

ViralScope AI is the foundation for a multimodal video intelligence and virality-prediction platform. This initial project establishes a modular monolith, API and database foundations, local development services, and tests. Video processing, machine learning, authentication, and product features are intentionally not implemented.

## Architecture

- **Backend:** Python 3.11, FastAPI, Pydantic v2, and SQLAlchemy 2.0.
- **Persistence:** PostgreSQL, configured through environment variables and managed with Alembic. No application tables or migrations exist yet.
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

Use URL-encoded characters if the database password contains reserved URL characters. `SECRET_KEY` is not used by this foundation; set a securely generated value before introducing features that need it.

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

The initial tests use FastAPI's in-process test client and do not require PostgreSQL or Redis.

## Database migrations

Alembic is configured, but there are no application tables or migrations yet. Once models and migrations are added, run Alembic commands from `backend\` so `alembic.ini` and the script directory resolve correctly.
