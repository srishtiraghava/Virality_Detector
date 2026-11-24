# Architecture

ViralScope AI starts as a modular monolith: one deployable backend with explicit module boundaries. This keeps local development and deployment straightforward while leaving room to separate workloads later if scale or ownership requires it.

## Repository layout

- `backend/app/api/v1/` — versioned HTTP routes and router composition.
- `backend/app/core/` — application configuration, logging, and cross-cutting concerns.
- `backend/app/database/` — SQLAlchemy declarative base, engine, session factory, and request dependency.
- `backend/app/models/` — SQLAlchemy persistence models for users, videos, and analysis jobs.
- `backend/app/schemas/` — Pydantic request, response, and job status contracts.
- `backend/app/repositories/` — SQLAlchemy persistence operations, including owner-scoped video access and analysis job lifecycle updates.
- `backend/app/services/` — future application services, grouped by domain (`upload`, `video`, `audio`, `visual`, `transcript`, `features`, `prediction`, and `recommendations`).
- `backend/app/ml/` — future preprocessing, training, inference, evaluation, calibration, and explainability code; no ML implementation is included.
- `backend/app/workers/` — reserved for future background jobs.
- `backend/app/utils/` — small shared utilities that do not belong to a domain module.
- `backend/tests/` — unit and integration test suites. The initial API tests do not require external services.
- `backend/alembic/` — Alembic environment and revision scripts.
- `frontend/` — reserved for a React, TypeScript, and Vite client.
- `research/` — future dataset documentation, notebooks, experiments, and evaluation work.
- `ml_artifacts/` — generated model artifacts; binaries are excluded from Git.
- `docs/` — project and architecture documentation.

## Runtime boundaries

FastAPI routes live under `/api/v1`. Configuration is loaded centrally with `pydantic-settings`; secrets are not logged. The SQLAlchemy engine is created without opening a database connection, and connections are made only when a session or the database health check is used. PostgreSQL is the configured database; Redis is provisioned for future local development but is not yet used by the application.

## Future extension guidance

Add domain behavior to services, persistence operations to repositories, and wire new HTTP endpoints through the versioned API router. The initial `User`, `Video`, and `AnalysisJob` models use UUID identifiers and UTC timestamps. A video can retain multiple analysis-job records; creating another job does not overwrite prior job history. PostgreSQL integration tests require an explicit `TEST_DATABASE_URL` and use a temporary isolated schema, while unit tests use SQLite in memory. Keep ML dependencies and generated artifacts out of the web request path unless a later, deliberate deployment design requires otherwise.
