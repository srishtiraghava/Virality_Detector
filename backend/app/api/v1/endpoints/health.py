import logging

from fastapi import APIRouter, HTTPException
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from app import __version__
from app.core.config import settings
from app.database.session import engine

logger = logging.getLogger("viralscope")

router = APIRouter()


@router.get("/health")
def health_check() -> dict[str, str]:
    return {
        "status": "ok",
        "version": __version__,
        "environment": settings.app_env,
    }


@router.get("/health/database")
def database_health_check() -> dict[str, str]:
    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
    except SQLAlchemyError as exc:
        logger.warning(
            "Database health check failed",
            extra={"exception_type": type(exc).__name__},
        )
        raise HTTPException(
            status_code=503,
            detail={"status": "unavailable", "database": "unavailable"},
        ) from None

    return {"status": "ok", "database": "available"}
