"""R-56 적대적 — `orders` 트리거의 execution_ref·intent 대조(DB층, task-1537).

Spec: docs/specs/L4_risk_and_safety_v1.0.md §4.1 I1·I4·I10, §3.6, §10(R-37
트리거). docs/design/INVARIANTS.md I-10(배선·우회불가·증명).

task-1520(51be3c7, repro 774a0a0)의 I10 재현을 **앱층 없이** DB에 직접 INSERT/
UPDATE해서 재현한다 — `fenced_submit`(task-1532)을 우회해도 마이그레이션
`a7c3d9e1f2b4`의 트리거만으로 거부돼야 한다. 모든 negative는 즉시 단언
(xfail/skip 없음)이고 통과하는 대조군을 함께 둔다.
"""
from __future__ import annotations

from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

import asyncpg
import pytest

from src.core.risk.decision import RiskOutcome
from src.foundation.connections.domain.models import HealthState
from src.foundation.risk_gate.adapters.postgres_decision_repository import (
    PostgresDecisionRepository,
)
from src.foundation.risk_gate.adapters.postgres_repository import PostgresRiskGateRepository
from src.foundation.risk_gate.application.evaluate_pre_submit import evaluate_pre_submit
from src.services.risk_decision_recorder import RiskDecisionRecorder
from tests.adversarial.risk.conftest import insert_decision, recorded_inputs, seed_execution
from tests.integration.conftest import NoopEventBus, create_test_user
from tests.integration.risk.test_pre_submit_gate import (
    _FakeConnectionRepo,
    _RiskRepoWithFixedSafetyState,
)

_MISMATCH = "INTEGRITY_RISK_FINGERPRINT_MISMATCH"
_OTHER_EXEC = "__other_execution__"
_INSERT_SQL = """
    INSERT INTO orders (
        user_id, client_order_id, strategy_id, strategy_version, execution_id, symbol,
        exchange, side, order_type, quantity, status, is_liquidation, asset_class,
        risk_decision_id
    ) VALUES ($1, $2, 'trigger-ref', '1.0.0', $3, $4, 'bitget', $5, 'MARKET', $6,
              'CREATED', FALSE, 'CRYPTO', $7)
    RETURNING order_id
"""


async def _insert(
    conn: asyncpg.Connection,
    user_id: UUID,
    decision_id: UUID,
    *,
    execution_id: int | None,
    symbol: str = "BTC/USDT",
    side: str = "BUY",
    quantity: Decimal = Decimal("0.01"),
) -> UUID:
    """앱층(`fenced_submit`)을 완전히 우회한 직접 INSERT — 트리거만이 방어선이다."""
    return await conn.fetchval(
        _INSERT_SQL, user_id, f"ref-{uuid4().hex}", execution_id, symbol, side, quantity,
        decision_id,
    )


def _rejected(field: str):
    return pytest.raises(asyncpg.CheckViolationError, match=f"{_MISMATCH} decision \\S+ {field}")


async def _tampered_decision(pool: asyncpg.Pool, victim: dict[str, Any], **changes: Any) -> UUID:
    """정직한 R-35 형식 스냅샷에서 키를 바꾸거나(값) 지운(`...`) 결정을 만든다."""
    ref = f"exec:{victim['execution_id']}"
    snapshot = await recorded_inputs(pool, victim["user_id"], execution_ref=ref)
    for key, value in changes.items():
        if value is ...:
            snapshot.pop(key)
        else:
            snapshot[key] = value
    decision = await insert_decision(
        pool, victim["user_id"], execution_ref=ref, inputs_snapshot=snapshot
    )
    return decision.decision_id


@pytest.fixture
async def victim(pool: asyncpg.Pool) -> dict[str, Any]:
    user_id = await create_test_user(pool)
    execution_id = await seed_execution(pool, user_id)
    decision = await insert_decision(pool, user_id, execution_ref=f"exec:{execution_id}")
    return {"user_id": user_id, "execution_id": execution_id, "decision_id": decision.decision_id}


# --- task-1520 I10 재현(DB층) ----------------------------------------------------


async def test_i10_db_layer_rejects_allow_transferred_to_other_execution_symbol_quantity(
    pool, victim
):
    """결정은 exec:X·BTC/USDT·BUY·0.01의 ALLOW. 공격자는 같은 tenant의 exec:Y·
    ETH/USDT·100 주문에 그 decision_id를 직접 INSERT한다(51be3c7 재현 원문).
    이전 트리거(93c0e7f6b8d9)는 통과시켰다 — 지금은 execution_ref에서 거부."""
    exec_y = await seed_execution(pool, victim["user_id"])
    async with pool.acquire() as conn:
        with _rejected("execution_ref"):
            await _insert(
                conn, victim["user_id"], victim["decision_id"], execution_id=exec_y,
                symbol="ETH/USDT", quantity=Decimal("100"),
            )
        count_sql = "SELECT count(*) FROM orders WHERE user_id = $1"
        assert await conn.fetchval(count_sql, victim["user_id"]) == 0
        # 대조군: 결정이 결속된 바로 그 subject는 통과한다.
        assert await _insert(
            conn, victim["user_id"], victim["decision_id"], execution_id=victim["execution_id"]
        )


@pytest.mark.parametrize(
    ("update", "field"),
    [
        ({"symbol": "ETH/USDT"}, "symbol"),
        ({"side": "SELL"}, "side"),
        ({"quantity": Decimal("0.02")}, "quantity"),
        ({"execution_id": _OTHER_EXEC}, "execution_ref"),
        ({"execution_id": None}, "execution_ref"),
    ],
    ids=["symbol", "side", "quantity", "other_execution", "null_execution"],
)
async def test_single_binding_field_mismatch_is_rejected(pool, victim, update, field):
    """tenant·outcome·만료는 전부 맞고 결속 필드 하나만 다르다 — 그 하나로 거부.
    `orders.execution_id`는 NULL 허용 컬럼: NULL이면 결속 증명 불가 → 거부."""
    if update.get("execution_id") == _OTHER_EXEC:
        update = {"execution_id": await seed_execution(pool, victim["user_id"])}
    kwargs = {"execution_id": victim["execution_id"], **update}
    async with pool.acquire() as conn:
        with _rejected(field):
            await _insert(conn, victim["user_id"], victim["decision_id"], **kwargs)


# --- NULL·결손·변조 스냅샷: fail-closed 경계 -------------------------------------


async def test_decision_with_null_execution_ref_is_never_actionable(pool, victim):
    ref = f"exec:{victim['execution_id']}"
    snapshot = await recorded_inputs(pool, victim["user_id"], execution_ref=ref)
    unbound = await insert_decision(
        pool, victim["user_id"], execution_ref=None, inputs_snapshot=snapshot,
    )
    async with pool.acquire() as conn:
        with _rejected("execution_ref"):
            await _insert(
                conn, victim["user_id"], unbound.decision_id, execution_id=victim["execution_id"]
            )


@pytest.mark.parametrize(
    ("drop", "field"),
    [
        (("symbol",), "symbol"),
        (("side",), "side"),
        (("quantity",), "quantity"),
        (("symbol", "side", "quantity"), "symbol"),
    ],
    ids=["no_symbol", "no_side", "no_quantity", "no_intent_at_all"],
)
async def test_snapshot_without_intent_key_is_fail_closed_even_for_matching_order(
    pool, victim, drop, field
):
    """negative — R-35 이전 형식(intent 키 부재) WORM 행은 subject를 증명할 수 없으므로
    주문이 실제로 같은 subject여도 거부한다(I-01 fail-closed, 앱층 `decision_binding`과
    같은 규칙). 첫 결손 키 순서(symbol→side→quantity)로 거부 문구가 정해진다."""
    legacy = await _tampered_decision(pool, victim, **{key: ... for key in drop})
    async with pool.acquire() as conn:
        with _rejected(field):
            await _insert(conn, victim["user_id"], legacy, execution_id=victim["execution_id"])


@pytest.mark.parametrize(
    ("key", "value"),
    [("quantity", "abc"), ("quantity", None), ("quantity", ""), ("symbol", None), ("side", None)],
    ids=["qty_unparseable", "qty_json_null", "qty_empty", "symbol_json_null", "side_json_null"],
)
async def test_present_but_invalid_intent_value_is_check_violation(pool, victim, key, value):
    """키가 있는데 값이 못 쓰는 경우 — 데이터 오류(22P02)가 새어 나오지 않고 같은
    check_violation으로 거부된다(호출부가 한 예외 클래스만 다루면 된다)."""
    tampered = await _tampered_decision(pool, victim, **{key: value})
    async with pool.acquire() as conn:
        with _rejected(key):
            await _insert(conn, victim["user_id"], tampered, execution_id=victim["execution_id"])


async def test_quantity_is_compared_numerically_not_textually(pool, victim):
    """`'0.0100'` = `0.01` — Decimal 직렬화 표기 차이로 정직한 주문을 거부하면 안 된다."""
    decision_id = await _tampered_decision(pool, victim, quantity="0.0100")
    async with pool.acquire() as conn:
        assert await _insert(
            conn, victim["user_id"], decision_id, execution_id=victim["execution_id"]
        )


# --- UPDATE 우회 -------------------------------------------------------------------


@pytest.mark.parametrize(
    ("column", "value", "field"),
    [
        ("symbol", "ETH/USDT", "symbol"),
        ("side", "SELL", "side"),
        ("quantity", Decimal("0.02"), "quantity"),
        ("execution_id", None, "execution_ref"),
        ("execution_id", _OTHER_EXEC, "execution_ref"),
    ],
    ids=["symbol", "side", "quantity", "null_execution", "other_execution"],
)
async def test_update_of_binding_columns_after_valid_insert_is_rejected(
    pool, victim, column, value, field
):
    """일치하는 행을 넣은 뒤 컬럼만 바꾸는 우회 — 트리거가 그 컬럼 UPDATE에도 발화."""
    if value == _OTHER_EXEC:
        value = await seed_execution(pool, victim["user_id"])
    async with pool.acquire() as conn:
        order_id = await _insert(
            conn, victim["user_id"], victim["decision_id"], execution_id=victim["execution_id"]
        )
        with _rejected(field):
            await conn.execute(
                f"UPDATE orders SET {column} = $2 WHERE order_id = $1", order_id, value  # noqa: S608
            )
        row = await conn.fetchrow(
            "SELECT symbol, side, quantity, execution_id FROM orders WHERE order_id = $1", order_id
        )
        assert tuple(row) == ("BTC/USDT", "BUY", Decimal("0.01"), victim["execution_id"])


# --- 기존 3검사 회귀 + 정적 배선 ---------------------------------------------------


async def test_prior_checks_still_fire_before_binding_checks(pool, victim):
    """tenant·outcome 거부는 그대로다(결속 검사가 앞선 검사를 대체하지 않았다)."""
    other = await create_test_user(pool)
    exec_other = await seed_execution(pool, other)
    foreign = await insert_decision(pool, other, execution_ref=f"exec:{exec_other}")
    deny = await insert_decision(
        pool, victim["user_id"], outcome=RiskOutcome.DENY,
        execution_ref=f"exec:{victim['execution_id']}",
    )
    async with pool.acquire() as conn:
        with _rejected("tenant"):
            await _insert(conn, victim["user_id"], foreign.decision_id, execution_id=exec_other)
        with pytest.raises(asyncpg.CheckViolationError, match="is not actionable"):
            await _insert(
                conn, victim["user_id"], deny.decision_id, execution_id=victim["execution_id"]
            )


async def test_trigger_definition_covers_binding_columns_and_function_has_checks(pool):
    """정적 배선 증명: 트리거가 4개 결속 컬럼 UPDATE에 발화하고, 함수 본문이
    execution_ref·symbol·side·quantity 검사를 담는다(마이그레이션 적용 상태)."""
    async with pool.acquire() as conn:
        trigger_def = await conn.fetchval(
            "SELECT pg_get_triggerdef(oid) FROM pg_trigger "
            "WHERE tgname = 'orders_require_risk_decision' AND NOT tgisinternal"
        )
        fn_def = await conn.fetchval(
            "SELECT pg_get_functiondef('orders_require_risk_decision_guard'::regproc)"
        )
    assert trigger_def is not None and fn_def is not None
    for column in ("execution_id", "symbol", "side", "quantity"):
        assert column in trigger_def.split(" ON ")[0], column
    for field in ("execution_ref", "symbol", "side", "quantity"):
        assert f"{_MISMATCH} decision % {field}" in fn_def, field
    assert "jsonb_exists" not in fn_def  # 키 부재를 통과시키는 조건부 대조가 없다


async def test_i10_wiring_real_pre_submit_snapshot_is_what_the_trigger_reads(pool):
    """I-10 — R-35 `evaluate_pre_submit`이 실제로 기록한 WORM 행의 키 이름·표기가
    트리거가 읽는 것과 같다: 같은 결정으로 수량·방향만 다른 직접 INSERT는 트리거만으로
    거부되고, 결정의 subject 그대로는 통과한다(TTL 안)."""
    user_id = await create_test_user(pool)
    execution_id = await seed_execution(pool, user_id)
    decision, _fence = await evaluate_pre_submit(
        _RiskRepoWithFixedSafetyState(
            PostgresRiskGateRepository(pool), cb_level="normal", distrust_level="NORMAL"
        ),
        _FakeConnectionRepo(tenant_id=user_id, provider_code="bitget", health=HealthState.HEALTHY),
        RiskDecisionRecorder(pool, PostgresDecisionRepository(pool), NoopEventBus()),
        tenant_id=user_id, execution_ref=f"exec:{execution_id}", provider_code="bitget",
        symbol="BTC/USDT", side="BUY", quantity=Decimal("0.01"), trace_id=uuid4(),
    )
    assert decision.outcome == RiskOutcome.ALLOW
    did = decision.decision_id
    async with pool.acquire() as conn:
        with _rejected("quantity"):
            await _insert(conn, user_id, did, execution_id=execution_id, quantity=Decimal("0.02"))
        with _rejected("side"):
            await _insert(conn, user_id, did, execution_id=execution_id, side="SELL")
        assert await _insert(conn, user_id, did, execution_id=execution_id)
