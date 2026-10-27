"""IND-12 negative — `GET /v1/indicators` rejects unauthenticated callers (I-10 wiring proof).

Split from test_indicators_router.py; uses the shared `client` fixture from conftest.py.
"""

from __future__ import annotations

from httpx import AsyncClient

PATH = "/v1/indicators"


async def test_requires_auth(client: AsyncClient) -> None:
    response = await client.get(PATH)
    assert response.status_code == 401
    body = response.json()
    assert body["error_code"].startswith("AUTH_")
    assert "trace_id" in body
