"""IND-12 negative — invalid cursor returns 400 (I-10 wiring proof).

Split from test_indicators_router.py; uses the shared `client` fixture from conftest.py.
"""

from __future__ import annotations

from httpx import AsyncClient

from tests.integration.api.test_indicators_router import _register

PATH = "/v1/indicators"


async def test_invalid_cursor_is_400(client: AsyncClient) -> None:
    headers, _ = await _register(client)
    response = await client.get(PATH, params={"cursor": "bad cursor!!"}, headers=headers)
    assert response.status_code == 400, response.text
    body = response.json()
    assert body["error_code"] == "VALIDATION_INVALID_FIELD"
    assert "trace_id" in body
