"""task-22842: RD-8/RD-17 transport validation and fail-closed error contract."""

import json
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import UUID

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from src.api.contracts.handlers import install_exception_handlers
from src.api.routers import research_data as routes


@pytest.fixture
async def research_client(monkeypatch):
    app = FastAPI()
    install_exception_handlers(app)
    app.include_router(routes.router)
    app.include_router(routes.search_router)
    repo = SimpleNamespace(
        list_by_tenant=AsyncMock(return_value=[]),
        list_by_tenant_across_sources=AsyncMock(return_value=[]),
        get_linked_instrument_ids=AsyncMock(return_value={}),
    )

    @asynccontextmanager
    async def acquire():
        yield object()

    app.dependency_overrides[routes.get_tenant_context] = lambda: SimpleNamespace(
        tenant_id=UUID(int=1)
    )
    app.dependency_overrides[routes.get_pool] = lambda: SimpleNamespace(acquire=acquire)
    app.dependency_overrides[routes.get_research_repository] = lambda: repo
    app.dependency_overrides[routes.get_source_contract_repository] = lambda: object()
    monkeypatch.setattr(routes, "authorize_source_read", AsyncMock())
    async with AsyncClient(
        transport=ASGITransport(app=app, raise_app_exceptions=False), base_url="http://test"
    ) as client:
        yield client, repo, app


@pytest.mark.parametrize("field", ["as_of", "published_from", "published_to"])
@pytest.mark.parametrize("value", ["2026-01-01T00:00:00", "not-a-date"])
async def test_get_rejects_invalid_coordinates_before_search(
    research_client, monkeypatch, field, value
):
    client, repo, _ = research_client
    search = Mock(side_effect=AssertionError("invalid input reached query"))
    monkeypatch.setattr(routes, "search_items", search)
    response = await client.get(
        "/v1/foundation/research/items", params={"source_id": "test", field: value}
    )
    assert response.status_code == 400
    assert response.json()["error_code"] == "VALIDATION_INVALID_FIELD"
    assert f"query.{field}" in response.json()["details"]["fields"]
    repo.list_by_tenant.assert_not_awaited()
    search.assert_not_called()


@pytest.mark.parametrize("value", ["2026-01-01T00:00:00", "not-a-date"])
async def test_post_rejects_invalid_as_of_before_search(research_client, monkeypatch, value):
    client, repo, _ = research_client
    search = Mock(side_effect=AssertionError("invalid input reached query"))
    monkeypatch.setattr(routes, "search_items", search)
    response = await client.post(
        "/v1/foundation/research-data/search", json={"query": "", "as_of": value}
    )
    assert response.status_code == 400
    assert response.json()["error_code"] == "VALIDATION_INVALID_FIELD"
    assert "body.as_of" in response.json()["details"]["fields"]
    repo.list_by_tenant_across_sources.assert_not_awaited()
    search.assert_not_called()


@pytest.mark.parametrize("value", ["2026-01-01T00:00:00Z", "2026-01-01T09:00:00+09:00"])
async def test_aware_coordinates_are_accepted(research_client, value):
    client, _, _ = research_client
    response = await client.get(
        "/v1/foundation/research/items",
        params={
            "source_id": "test",
            "as_of": value,
            "published_from": value,
            "published_to": value,
        },
    )
    assert response.status_code == 200
    assert response.json()["data"] == []
    response = await client.post(
        "/v1/foundation/research-data/search", json={"query": "", "as_of": value}
    )
    assert response.status_code == 200
    assert response.json()["data"]["items"] == []


async def test_unexpected_query_value_error_fails_closed(research_client, monkeypatch):
    client, _, _ = research_client
    monkeypatch.setattr(
        routes, "search_items", Mock(side_effect=ValueError("private kernel fault"))
    )
    response = await client.post("/v1/foundation/research-data/search", json={"query": ""})
    assert response.status_code == 500
    assert response.json()["error_code"] == "INTERNAL_ERROR"
    assert "data" not in response.json()
    assert "private kernel fault" not in response.text


async def test_openapi_matches_validation_envelope(research_client):
    _, _, app = research_client
    schema = app.openapi()
    snapshot = json.loads(
        (Path(__file__).resolve().parents[3] / "contracts/openapi/v1.json").read_text(
            encoding="utf-8"
        )
    )
    for path, method in [
        ("/v1/foundation/research/items", "get"),
        ("/v1/foundation/research-data/search", "post"),
    ]:
        responses = schema["paths"][path][method]["responses"]
        assert snapshot["paths"][path][method]["responses"] == responses
        assert "422" not in responses
        assert responses["400"]["content"]["application/json"]["schema"] == {
            "$ref": "#/components/schemas/ApiError"
        }
