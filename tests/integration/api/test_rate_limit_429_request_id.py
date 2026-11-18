"""Rate-limit 429 response — X-Request-ID handling.

Spec: docs/specs/L4_platform_observability_tenancy_api_v1.0.md §9 PLT-25
"""

from __future__ import annotations

import re
from collections.abc import AsyncGenerator

import pytest
from httpx import ASGITransport, AsyncClient

from src.api.contracts.error_codes import ErrorCode
from src.core.rate_limit.limiter import Decision, set_limiter
from src.core.rate_limit.policy import RateLimitPolicy
from src.main import app

_HEX32 = re.compile(r"^[0-9a-f]{32}$")
_ULID = "01M7PM0JK2XG3E6P15WD6C7DKN"


class _AlwaysRejectLimiter:
    async def acquire(self, policy: RateLimitPolicy, key: str) -> Decision:
        return Decision(allowed=False, retry_after_s=1, remaining=0)


@pytest.fixture
async def client() -> AsyncGenerator[AsyncClient]:
    set_limiter(_AlwaysRejectLimiter())
    async with app.router.lifespan_context(app):
        transport = ASGITransport(app=app, raise_app_exceptions=False)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            yield ac


async def test_ulid_request_id_is_echoed_on_429(client: AsyncClient) -> None:
    response = await client.get("/openapi.json", headers={"X-Request-ID": _ULID})
    assert response.status_code == 429
    assert response.json()["error_code"] == ErrorCode.RATE_LIMIT_EXCEEDED.value
    assert response.headers["X-Request-ID"] == _ULID


@pytest.mark.parametrize("bad", ["", "a b", "x" * 200, "id;drop"])
async def test_unsafe_request_id_is_replaced_on_429(client: AsyncClient, bad: str) -> None:
    response = await client.get("/openapi.json", headers={"X-Request-ID": bad})
    assert response.status_code == 429
    assert _HEX32.match(response.headers["X-Request-ID"])


async def test_missing_request_id_generated_on_429(client: AsyncClient) -> None:
    response = await client.get("/openapi.json")
    assert response.status_code == 429
    assert _HEX32.match(response.headers["X-Request-ID"])
