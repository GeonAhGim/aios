"""J8 trail W3b 통합 — GET /executions/{id}/decision-trail.

픽스처와 헬퍼는 test_executions_router.py에서 가져온다(live_conversion 라우터 테스트와 같은 방식).
"""

# ruff: noqa: F811  -- 픽스처를 인자 이름으로 다시 받는 pytest 관례
from tests.integration.test_executions_router import (  # noqa: F401
    STRONG_PASSWORD,
    _create_approved_strategy,
    _link_credential,
    _register,
    client,
    event_bus,
    pool,
)


async def _expanded_live(client, pool):
    headers, user_id = await _register(client)
    strategy_id, version = await _create_approved_strategy(pool, user_id, certified_badge=True)
    await _link_credential(pool, user_id)
    created = await client.post(
        "/executions",
        json={
            "strategy_id": strategy_id,
            "strategy_version": version,
            "allocated_capital": "500",
            "currency": "USDT",
            "exchange": "bitget",
            "mode": "PAPER",
        },
        headers=headers,
    )
    live = await client.post(
        f"/executions/{created.json()['id']}/live-conversion",
        json={
            "allocated_capital": "500",
            "currency": "USDT",
            "exchange": "bitget",
            "max_loss_amount": "25",
            "initial_exposure_pct": "10",
            "password": STRONG_PASSWORD,
        },
        headers=headers,
    )
    live_id = live.json()["id"]
    expanded = await client.post(
        f"/executions/{live_id}/expand-exposure",
        json={
            "exposure_pct": "50",
            "currency": "USDT",
            "exchange": "bitget",
            "password": STRONG_PASSWORD,
        },
        headers=headers,
    )
    assert expanded.status_code == 200
    return headers, live_id


async def test_decision_trail_lists_conversion_then_expansion_in_order(client, pool):
    headers, live_id = await _expanded_live(client, pool)
    response = await client.get(f"/executions/{live_id}/decision-trail", headers=headers)
    assert response.status_code == 200
    entries = response.json()["entries"]
    kinds = [e["kind"] for e in entries if e["kind"] in ("CONVERSION", "EXPANSION")]
    assert kinds == ["CONVERSION", "EXPANSION"]
    times = [e["occurred_at"] for e in entries]
    assert times == sorted(times)


async def test_decision_trail_requires_authentication(client, pool):
    response = await client.get("/executions/1/decision-trail")
    assert response.status_code == 401


async def test_decision_trail_rejects_another_users_execution(client, pool):
    _headers, live_id = await _expanded_live(client, pool)
    other_headers, _ = await _register(client)
    response = await client.get(f"/executions/{live_id}/decision-trail", headers=other_headers)
    assert 400 <= response.status_code < 500
    assert response.status_code != 401
    assert "entries" not in response.json()


async def test_decision_trail_unknown_execution_is_rejected(client, pool):
    headers, _ = await _register(client)
    response = await client.get("/executions/999999999/decision-trail", headers=headers)
    assert 400 <= response.status_code < 500
    assert "entries" not in response.json()
