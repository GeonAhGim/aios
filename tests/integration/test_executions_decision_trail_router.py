"""J8 W3b — GET /executions/{id}/decision-trail 통합 테스트.

픽스처와 헬퍼는 test_executions_router / test_executions_live_conversion_router에서 가져온다.
"""

# ruff: noqa: F811  -- 픽스처를 인자 이름으로 다시 받는 pytest 관례
from tests.integration.test_executions_live_conversion_router import (  # noqa: F401
    _paper_then_live,
)
from tests.integration.test_executions_router import (  # noqa: F401
    STRONG_PASSWORD,
    _register,
    client,
    event_bus,
    pool,
)


async def test_trail_lists_conversion_then_expansion_in_time_order(client, pool):
    headers, live = await _paper_then_live(client, pool, pct="10", max_loss="25")
    execution_id = live.json()["id"]
    expanded = await client.post(
        f"/executions/{execution_id}/expand-exposure",
        json={
            "exposure_pct": "50",
            "currency": "USDT",
            "exchange": "bitget",
            "password": STRONG_PASSWORD,
        },
        headers=headers,
    )
    assert expanded.status_code == 200

    response = await client.get(f"/executions/{execution_id}/decision-trail", headers=headers)

    assert response.status_code == 200
    entries = response.json()["entries"]
    assert len(entries) == 2
    assert entries[0]["occurred_at"] <= entries[1]["occurred_at"]
    assert entries[0]["kind"] != entries[1]["kind"]


async def test_trail_requires_authentication(client, pool):
    _headers, live = await _paper_then_live(client, pool)
    response = await client.get(f"/executions/{live.json()['id']}/decision-trail")
    assert response.status_code == 401


async def test_trail_rejects_another_users_execution(client, pool):
    _headers, live = await _paper_then_live(client, pool)
    other_headers, _other_id = await _register(client)

    response = await client.get(
        f"/executions/{live.json()['id']}/decision-trail", headers=other_headers
    )

    assert 400 <= response.status_code < 500
    assert "entries" not in response.json()


async def test_trail_unknown_execution_is_rejected(client, pool):
    headers, _user_id = await _register(client)
    response = await client.get("/executions/999999999/decision-trail", headers=headers)
    assert 400 <= response.status_code < 500
    assert "entries" not in response.json()
