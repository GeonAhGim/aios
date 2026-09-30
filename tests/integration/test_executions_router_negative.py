"""16번대 통합테스트 — /executions 라우터 DEEPEN(negative/실패주입).

원 리프 test_executions_router.py의 고아 산출물 회수(task-6704/9400) —
불변식 위반 입력 거부 3건 + 의존성 장애 실패주입 1건. 픽스처·헬퍼는
test_executions_router.py와 동일 패턴을 재사용한다(client/pool/_register 등은
그 파일에서 import — 중복 정의로 인한 드리프트를 피한다).
"""

import uuid

from src.api.service_deps import get_credential_resolver
from src.main import app
from tests.integration.test_executions_router import (  # noqa: F401
    _activate_mandate,
    _create_approved_strategy,
    _link_credential,
    _override_resolver,
    _register,
    client,
    event_bus,
    pool,
)


async def test_create_execution_invalid_mode_rejected(client, pool):  # noqa: F811
    """mode가 VALID_MODES('PAPER'/'LIVE')에 없으면 ExecutionCreateError ->
    400/VALIDATION_INVALID_FIELD로 거부돼야 한다(execution_service.py:98)."""
    headers, user_id = await _register(client)
    strategy_id, version = await _create_approved_strategy(pool, user_id)
    await _link_credential(pool, user_id)
    await _activate_mandate(pool, user_id)

    response = await client.post(
        "/executions",
        json={
            "strategy_id": strategy_id,
            "strategy_version": version,
            "allocated_capital": "500",
            "currency": "USDT",
            "exchange": "bitget",
            "mode": "HYPER_LEVERAGE",
        },
        headers=headers,
    )

    assert response.status_code == 400
    assert response.json()["error_code"] == "VALIDATION_INVALID_FIELD"


async def test_create_execution_nonexistent_strategy_rejected(client, pool):  # noqa: F811
    """존재하지 않는 (strategy_id, strategy_version) 조합은 ExecutionCreateError ->
    400으로 거부돼야 한다(execution_service.py:110-111) — FK 없이도 조기 차단."""
    headers, user_id = await _register(client)
    await _activate_mandate(pool, user_id)

    response = await client.post(
        "/executions",
        json={
            "strategy_id": f"no-such-strategy-{uuid.uuid4().hex[:8]}",
            "strategy_version": "1.0.0",
            "allocated_capital": "500",
            "currency": "USDT",
            "exchange": "bitget",
            "mode": "PAPER",
        },
        headers=headers,
    )

    assert response.status_code == 400
    assert response.json()["error_code"] == "VALIDATION_INVALID_FIELD"


async def test_create_execution_without_linked_credential_rejected(client, pool):  # noqa: F811
    """exchange_credentials에 연동이 없으면 ExecutionCreateError -> 400으로
    거부돼야 한다(execution_service.py:124-125) — 클라이언트가 잔고 조회 mock으로
    성공했더라도 DB의 실제 자격증명 여부가 최종 게이트다."""
    headers, user_id = await _register(client)
    strategy_id, version = await _create_approved_strategy(pool, user_id)
    await _activate_mandate(pool, user_id)
    # 의도적으로 _link_credential을 호출하지 않는다.

    response = await client.post(
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

    assert response.status_code == 400
    assert response.json()["error_code"] == "VALIDATION_INVALID_FIELD"


async def test_create_execution_exchange_balance_lookup_failure_fails_closed(
    client,  # noqa: F811
    pool,  # noqa: F811
):
    """실패주입: CredentialResolver.get_adapter().get_balance()가 거래소 장애로
    예외를 던지면 fail-closed(105 표준) — 실행 생성이 201로 성공하면 안 되고, 부분
    상태(allocated_capital 미확인 채 INSERT)로 새지 않아야 한다."""
    headers, user_id = await _register(client)
    strategy_id, version = await _create_approved_strategy(pool, user_id)
    await _link_credential(pool, user_id)
    await _activate_mandate(pool, user_id)

    class _FailingAdapter:
        async def get_balance(self):
            raise RuntimeError("exchange API unreachable")

    class _FailingResolver:
        async def get_adapter(self, user_id, exchange):
            return _FailingAdapter()

    app.dependency_overrides[get_credential_resolver] = lambda: _FailingResolver()
    try:
        response = await client.post(
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
    finally:
        app.dependency_overrides[get_credential_resolver] = _override_resolver

    assert response.status_code != 201

    list_response = await client.get("/executions", headers=headers)
    assert list_response.status_code == 200
    assert list_response.json() == []
