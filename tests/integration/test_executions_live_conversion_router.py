"""J8 LP-3/LP-4 통합 — /executions 전환(live-conversion)·노출 확대 라우터.

test_executions_router.py가 500줄을 넘어 전환 관련 테스트를 이 파일로 옮겼다.
픽스처와 헬퍼는 같은 모듈에서 가져온다(test_executions_router_negative.py와 같은 방식).
"""

# ruff: noqa: F811  -- 픽스처를 인자 이름으로 다시 받는 pytest 관례
from decimal import Decimal

from tests.integration.test_executions_router import (  # noqa: F401
    STRONG_PASSWORD,
    _activate_mandate,
    _create_approved_strategy,
    _link_credential,
    _override_resolver,
    _register,
    client,
    event_bus,
    pool,
)


async def test_convert_paper_to_live(client, pool):
    headers, user_id = await _register(client)
    strategy_id, version = await _create_approved_strategy(pool, user_id, certified_badge=True)
    await _link_credential(pool, user_id)
    create_response = await client.post(
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
    execution_id = create_response.json()["id"]

    response = await client.post(
        f"/executions/{execution_id}/live-conversion",
        json={
            "allocated_capital": "500",
            "currency": "USDT",
            "exchange": "bitget",
            "max_loss_amount": "100",
            "initial_exposure_pct": "10",
            "password": STRONG_PASSWORD,
        },
        headers=headers,
    )

    assert response.status_code == 201
    assert response.json()["mode"] == "LIVE"
    assert response.json()["approval_request_id"] is not None


async def test_convert_to_live_rejects_wrong_password_and_creates_nothing(client, pool):
    """J8 LP-3: 세션 탈취로는 전환할 수 없다 -- 재인증 실패는 403이고 LIVE 실행이 생기지 않는다."""
    headers, user_id = await _register(client)
    strategy_id, version = await _create_approved_strategy(pool, user_id, certified_badge=True)
    await _link_credential(pool, user_id)
    create_response = await client.post(
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
    execution_id = create_response.json()["id"]

    response = await client.post(
        f"/executions/{execution_id}/live-conversion",
        json={
            "allocated_capital": "500",
            "currency": "USDT",
            "exchange": "bitget",
            "max_loss_amount": "100",
            "initial_exposure_pct": "10",
            "password": "Wr0ng!Passw0rd",
        },
        headers=headers,
    )

    assert response.status_code == 403
    async with pool.acquire() as conn:
        live_count = await conn.fetchval(
            "SELECT count(*) FROM strategy_executions WHERE converted_from_execution_id = $1",
            execution_id,
        )
    assert live_count == 0


async def test_convert_to_live_rejects_loss_limit_above_allocation(client, pool):
    headers, user_id = await _register(client)
    response = await client.post(
        "/executions/1/live-conversion",
        json={
            "allocated_capital": "500",
            "currency": "USDT",
            "exchange": "bitget",
            "max_loss_amount": "501",
            "initial_exposure_pct": "10",
            "password": STRONG_PASSWORD,
        },
        headers=headers,
    )
    assert response.status_code == 400  # 스키마 검증 -- 서비스·재인증까지 가지 않는다


async def _paper_then_live(client, pool, *, pct="10", max_loss="25"):
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
            "max_loss_amount": max_loss,
            "initial_exposure_pct": pct,
            "password": STRONG_PASSWORD,
        },
        headers=headers,
    )
    return headers, live


async def test_convert_to_live_response_shows_the_staged_capital_not_the_target(client, pool):
    """J8 LP-4: 목표 500의 10%만 열린다 -- 응답이 목표를 허용 자본인 척 돌려주면 안 된다."""
    _headers, live = await _paper_then_live(client, pool, pct="10", max_loss="25")
    assert live.status_code == 201
    body = live.json()
    assert Decimal(str(body["allocated_capital"])) == Decimal("50")
    assert Decimal(str(body["max_drawdown_pct"])) == Decimal("50")  # 25 / 50


async def test_expand_exposure_requires_reauthentication(client, pool):
    headers, live = await _paper_then_live(client, pool)
    execution_id = live.json()["id"]
    response = await client.post(
        f"/executions/{execution_id}/expand-exposure",
        json={
            "exposure_pct": "50",
            "currency": "USDT",
            "exchange": "bitget",
            "password": "Wr0ng!Passw0rd",
        },
        headers=headers,
    )
    assert response.status_code == 403
    async with pool.acquire() as conn:
        allocated = await conn.fetchval(
            "SELECT allocated_capital FROM strategy_executions WHERE id = $1", execution_id
        )
    assert allocated == Decimal("50")  # 확대되지 않았다


async def test_expand_exposure_succeeds_with_reauthentication_and_only_grows(client, pool):
    headers, live = await _paper_then_live(client, pool, pct="10", max_loss="25")
    execution_id = live.json()["id"]
    body = {
        "exposure_pct": "50",
        "currency": "USDT",
        "exchange": "bitget",
        "password": STRONG_PASSWORD,
    }
    ok = await client.post(
        f"/executions/{execution_id}/expand-exposure", json=body, headers=headers
    )
    assert ok.status_code == 200
    assert Decimal(str(ok.json()["allocated_capital"])) == Decimal("250")
    assert Decimal(str(ok.json()["max_drawdown_pct"])) == Decimal("10")  # 25 / 250
    # 같은 값으로 다시 -> 확대가 아니므로 거부(400대)
    again = await client.post(
        f"/executions/{execution_id}/expand-exposure", json=body, headers=headers
    )
    assert 400 <= again.status_code < 500


async def test_deprecated_convert_to_live_refuses_and_creates_nothing(client, pool):
    """폐기된 경로는 안전장치(손실 한도·초기 노출·재인증)를 우회하는 통로가 아니다.
    410이고 아무것도 만들지 않는다."""
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
    execution_id = created.json()["id"]

    # 옛 요청 모양(세 필드) 그대로 -- v1 계약이 허용하는 형태
    response = await client.post(
        f"/executions/{execution_id}/convert-to-live",
        json={"allocated_capital": "500", "currency": "USDT", "exchange": "bitget"},
        headers=headers,
    )

    assert response.status_code == 410
    assert "live-conversion" in response.text
    async with pool.acquire() as conn:
        live_count = await conn.fetchval(
            "SELECT count(*) FROM strategy_executions WHERE converted_from_execution_id = $1",
            execution_id,
        )
    assert live_count == 0
