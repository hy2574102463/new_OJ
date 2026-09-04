import inspect
from unittest.mock import AsyncMock

from fastapi import FastAPI
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient
from pydantic import BaseModel

from app.api.system import router as system_router
from app.core.config import Settings
from app.core.exceptions import AppError
from app.main import create_app


def test_health_reports_database_status(client: TestClient) -> None:
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {
        "code": 200,
        "msg": "success",
        "data": {"status": "healthy", "database": "ok"},
    }
    assert response.headers["X-Request-ID"]


def test_reset_is_available_in_test_environment(client: TestClient) -> None:
    response = client.post("/api/reset/")

    assert response.status_code == 200
    assert response.json() == {
        "code": 200,
        "msg": "system reset successfully",
        "data": None,
    }


def test_reset_is_hidden_outside_test_environment(tmp_path) -> None:
    settings = Settings(
        environment="development",
        database_path=tmp_path / "development.db",
        test_reset_enabled=True,
    )
    with TestClient(create_app(settings)) as client:
        response = client.post("/api/reset/")

    assert response.status_code == 404
    assert response.json() == {"code": 404, "msg": "not found", "data": None}


def test_validation_errors_are_mapped_to_400(test_settings: Settings) -> None:
    class Payload(BaseModel):
        count: int

    app = create_app(test_settings)

    @app.post("/validated")
    async def validated(_payload: Payload) -> dict[str, bool]:
        return {"ok": True}

    with TestClient(app) as client:
        response = client.post("/validated", json={"count": "not-an-integer"})

    assert response.status_code == 400
    assert response.json() == {"code": 400, "msg": "invalid request", "data": None}


def test_application_errors_keep_their_status(test_settings: Settings) -> None:
    app = create_app(test_settings)

    @app.get("/conflict")
    async def conflict() -> None:
        raise AppError(409, "resource conflict")

    with TestClient(app) as client:
        response = client.get("/conflict")

    assert response.status_code == 409
    assert response.json() == {
        "code": 409,
        "msg": "resource conflict",
        "data": None,
    }


def test_unexpected_errors_are_sanitized(test_settings: Settings) -> None:
    app = create_app(test_settings)
    secret = "/private/server/path/api-key"

    @app.get("/explode")
    async def explode() -> None:
        raise RuntimeError(secret)

    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.get("/explode")

    assert response.status_code == 500
    assert response.json() == {
        "code": 500,
        "msg": "internal server error",
        "data": None,
    }
    assert secret not in response.text


def test_database_failure_does_not_leak_details(client: TestClient) -> None:
    secret = "/private/database/path"
    app: FastAPI = client.app
    app.state.database.ping = AsyncMock(side_effect=RuntimeError(secret))

    response = client.get("/health")

    assert response.status_code == 500
    assert secret not in response.text


def test_public_application_routes_are_async() -> None:
    routes = [route for route in system_router.routes if isinstance(route, APIRoute)]

    assert routes
    assert all(inspect.iscoroutinefunction(route.endpoint) for route in routes)
