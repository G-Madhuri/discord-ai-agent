"""API smoke tests, including the first-milestone health contract."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.main import create_app


@pytest.fixture
def client() -> TestClient:
    with TestClient(create_app()) as c:
        yield c


def test_health_returns_status_ok(client: TestClient):
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_health_does_not_require_a_database(client: TestClient):
    # The app is created without any database being reachable.
    assert client.get("/health").status_code == 200


def test_openapi_exposes_the_assignment_endpoint(client: TestClient):
    paths = client.get("/openapi.json").json()["paths"]

    assert "/api/v1/assignments/assign" in paths
    assert "/api/v1/assignments/history" in paths
    assert "/health" in paths
