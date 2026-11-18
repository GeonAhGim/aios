"""Regression: a non-UUID X-Request-ID (e.g. a browser-generated ULID) must not turn
POST /auth/login with bad credentials into a 500 (UAT task-25642)."""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

from src.main import app

ULID = "01M7PM0JK2XG3E6P15WD6C7DKN"
UUID_ID = "6f1c0a52-3b7e-4c1e-9a47-0c1d2e3f4a5b"


@pytest.fixture
async def client():
    async with app.router.lifespan_context(app):
        transport = ASGITransport(app=app, raise_app_exceptions=False)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            yield ac


async def _bad_login(client: AsyncClient, request_id: str | None):
    headers = {"X-Request-Id": request_id} if request_id is not None else {}
    return await client.post(
        "/auth/login",
        json={"email": "nobody-ulid@example.com", "password": "Wrong!Passw0rd1"},
        headers=headers,
    )


@pytest.mark.parametrize("request_id", [UUID_ID, ULID, "arbitrary-token_123"])
async def test_bad_login_with_accepted_request_id_is_401(client, request_id):
    response = await _bad_login(client, request_id)
    assert response.status_code == 401
    assert response.headers["X-Request-ID"]


@pytest.mark.parametrize("request_id", ["x" * 500, "bad id\twith space", "../../etc"])
async def test_bad_login_with_abnormal_request_id_is_not_500(client, request_id):
    response = await _bad_login(client, request_id)
    assert response.status_code == 401
    echoed = response.headers.get("X-Request-ID")
    assert echoed and len(echoed) <= 128
