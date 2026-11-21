import httpx
import pytest

from app import __version__
from app.core.config import settings
from app.main import app


@pytest.mark.anyio
async def test_root_endpoint(client: httpx.AsyncClient) -> None:
    response = await client.get("/")

    assert response.status_code == 200
    assert response.json() == {
        "application": settings.app_name,
        "status": "ok",
    }


@pytest.mark.anyio
async def test_health_endpoint(client: httpx.AsyncClient) -> None:
    response = await client.get("/api/v1/health")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "version": __version__,
        "environment": settings.app_env,
    }


def test_application_metadata() -> None:
    assert app.title == settings.app_name
    assert app.version == __version__
    assert app.description == "A modular foundation for multimodal video intelligence."
