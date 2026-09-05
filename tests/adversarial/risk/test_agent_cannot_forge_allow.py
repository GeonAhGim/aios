"""R-56 적대적 — RSK-006: 에이전트·라우터 입력으로 ALLOW를 위조할 수 없다.

Spec: docs/specs/L4_risk_and_safety_v1.0.md §3.6 2~4단계, §4.1 I1(Master
Authority)·I7(WORM)·I10, §8 적대적 "RSK-006 risk_decision 위조 시도 →
fingerprint 불일치, 타 테넌트 decision_id 사용 거부; WORM 우회(직접 SQL)
실패", §9 R-56(선행 R-37 `62aa2a9` fenced_submit + 트리거 `93c0e7f6b8d9`).

공격자 모델: `submit_with_fence`에 도달할 수 있는 코드(에이전트 도구·라우터)
가 `GateDecision`을 마음대로 구성한다 — outcome=ALLOW, 임의 decision_id,
위조된 fence 스냅샷. 권위는 WORM `risk_decision` 행 + `orders` BEFORE
INSERT 트리거뿐이어야 한다(I1). 모든 케이스에서 (a) 거래소 어댑터 호출 0,
(b) `orders` 행 0(claim조차 남지 않음), (c) 참조된 WORM 행이 공격 전후로
동일함을 단언한다.

hash 불일치의 재현: WORM에 존재하지 않는 decision_id(어떤 fingerprint와도
대응하지 않는 "지어낸 결정")와, 기록된 DENY/만료 결정의 hash·outcome·
tenant·expires_at을 주문에 맞게 고쳐 쓰려는 직접 SQL — 둘 다 거부.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from typing import Any
from uuid import UUID, uuid4

import asyncpg
import pytest

from src.core.risk.decision import RiskDecision, RiskOutcome
from src.foundation.risk_gate.adapters.postgres_decision_repository import (
    PostgresDecisionRepository,
)
from src.services.order_service.fenced_submit import RiskDecisionMissingError, submit_with_fence
from src.services.order_service.gate import GateDecision, GateOutcome
from src.services.order_service.worm_decision_check import RiskDecisionIntegrityError
from tests.adversarial.risk.conftest import (
    RecordingAdapter,
    fence_reader,
    insert_decision,
    make_order,
    seed_execution,
)
from tests.integration.conftest import create_test_user

_TRIGGER_ERRORS = (asyncpg.CheckViolationError, asyncpg.ForeignKeyViolationError)
_WORM_ERRORS = (asyncpg.InsufficientPrivilegeError, asyncpg.RaiseError)


@dataclass(frozen=True)
class _Victim:
    user_id: UUID
    execution_id: int
    f0: dict[str, int]


@pytest.fixture
async def victim(pool: asyncpg.Pool) -> _Victim:
    user_id = await create_test_user(pool)
    execution_id = await seed_execution(pool, user_id)
    f0 = dict(await fence_reader(pool, user_id, execution_id)())
    return _Victim(user_id, execution_id, f0)


@pytest.fixture
def decision_repo(pool: asyncpg.Pool) -> PostgresDecisionRepository:
    return PostgresDecisionRepository(pool)


def _forged_gate(decision_id: UUID | None, f0: dict[str, int]) -> GateDecision:
    """에이전트가 손으로 만든 '허용' 결정 — outcome은 항상 ALLOW로 위조."""
    return GateDecision(outcome=GateOutcome.ALLOW, fence_snapshot=f0, decision_id=decision_id)


async def _orders_for(pool: asyncpg.Pool, user_id: UUID) -> int:
    async with pool.acquire() as conn:
        return await conn.fetchval("SELECT count(*) FROM orders WHERE user_id = $1", user_id)


async def _snapshot(
    repo: PostgresDecisionRepository, decision_id: UUID
) -> tuple[RiskDecision, dict[str, Any]] | None:
    return await repo.get(decision_id)


async def _attack(
    pool: asyncpg.Pool,
    victim: _Victim,
    gate: GateDecision,
    expected: tuple[type[BaseException], ...] = _TRIGGER_ERRORS,
) -> BaseException:
    """위조 gate로 제출을 시도한다. 예외 종류를 확인하고, 부작용 0을 단언한다."""
    adapter = RecordingAdapter()
    before = await _orders_for(pool, victim.user_id)
    with pytest.raises(expected) as exc_info:
        await submit_with_fence(
            pool, adapter, make_order(victim.execution_id), user_id=victim.user_id,
            gate_decision=gate, read_fences=fence_reader(pool, victim.user_id, victim.execution_id),
            decision_reader=PostgresDecisionRepository(pool),
        )
    assert adapter.place_order_call_count == 0, "위조 결정으로 거래소에 도달했다(I1 위반)"
    assert adapter.cancelled_exchange_order_ids == []
    assert await _orders_for(pool, victim.user_id) == before, "위조 결정으로 orders 행이 남았다"
    return exc_info.value


async def test_rsk006_missing_decision_id_is_refused_before_claim(pool, victim):
    await _attack(pool, victim, _forged_gate(None, victim.f0), (RiskDecisionMissingError,))


async def test_rsk006_invented_decision_id_matches_no_worm_row(pool, victim, decision_repo):
    """WORM에 기록되지 않은 decision_id = 어떤 fingerprint/inputs_hash와도
    대응하지 않는 결정. task-1532부터는 claim 전 WORM 재조회가 먼저 거부한다
    (트리거 'does not exist'는 그 뒤의 2차 방어)."""
    invented = uuid4()
    assert await _snapshot(decision_repo, invented) is None
    error = await _attack(
        pool, victim, _forged_gate(invented, victim.f0), (RiskDecisionIntegrityError,)
    )
    assert "INTEGRITY_RISK_FINGERPRINT_MISMATCH" in str(error)
    assert error.mismatches == ("decision_missing",)


async def test_rsk006_other_tenants_allow_decision_is_rejected(pool, victim, decision_repo):
    other_tenant = await create_test_user(pool)
    stolen = await insert_decision(pool, other_tenant, execution_ref=f"exec:{victim.execution_id}")
    before = await _snapshot(decision_repo, stolen.decision_id)

    error = await _attack(
        pool, victim, _forged_gate(stolen.decision_id, victim.f0), (RiskDecisionIntegrityError,)
    )

    assert "INTEGRITY_RISK_FINGERPRINT_MISMATCH" in str(error)
    assert error.mismatches == ("decision_missing",)  # 타 tenant 행은 존재 여부조차 새지 않는다
    assert await _snapshot(decision_repo, stolen.decision_id) == before  # 피해 tenant 행 불변


@pytest.mark.parametrize("outcome", [RiskOutcome.DENY, RiskOutcome.PAUSE, RiskOutcome.ESCALATE])
async def test_rsk006_non_actionable_decision_with_outcome_flipped_in_memory(
    pool, victim, decision_repo, outcome
):
    """에이전트가 자기 tenant의 진짜 DENY/PAUSE/ESCALATE 결정 id를 들고 outcome만
    ALLOW로 바꿔 제출한다 — 권위는 메모리의 GateDecision이 아니라 WORM 행."""
    recorded = await insert_decision(
        pool, victim.user_id, outcome=outcome, execution_ref=f"exec:{victim.execution_id}"
    )
    error = await _attack(pool, victim, _forged_gate(recorded.decision_id, victim.f0))
    assert "is not actionable" in str(error)
    got = await _snapshot(decision_repo, recorded.decision_id)
    assert got is not None and got[0].outcome == outcome


async def test_rsk006_expired_allow_decision_is_rejected(pool, victim):
    expired = await insert_decision(
        pool, victim.user_id, ttl=timedelta(seconds=-1), execution_ref=f"exec:{victim.execution_id}"
    )
    error = await _attack(pool, victim, _forged_gate(expired.decision_id, victim.f0))
    assert "RISK_DECISION_EXPIRED" in str(error)


async def test_rsk006_forged_fence_snapshot_cannot_revive_denied_decision(pool, victim):
    """fence 스냅샷을 아무리 유리하게 위조해도(토큰 ∞) 통과 못 한다 —
    task-1532부터 호출자 F0는 WORM F0와 같아야 하며(I4) 다르면 결정 outcome을
    보기도 전에 claim 전 거부된다. 결정 자체가 DENY인 것은 트리거가 2차로 막는다."""
    denied = await insert_decision(
        pool, victim.user_id, outcome=RiskOutcome.DENY, execution_ref=f"exec:{victim.execution_id}"
    )
    inflated = {pair: 2**40 for pair in victim.f0}
    error = await _attack(
        pool, victim, _forged_gate(denied.decision_id, inflated), (RiskDecisionIntegrityError,)
    )
    assert error.mismatches == ("fence_snapshot",)


# --- WORM 우회: 결정을 주문에 맞게 고쳐 쓰기 -------------------------------


async def _tamper(pool: asyncpg.Pool, sql: str, *args: object) -> None:
    with pytest.raises(_WORM_ERRORS) as exc_info:
        async with pool.acquire() as conn, conn.transaction():
            await conn.execute("SET ROLE aios_app")
            await conn.execute(sql, *args)
    if isinstance(exc_info.value, asyncpg.RaiseError):
        assert "append-only violation" in str(exc_info.value)


@pytest.mark.parametrize(
    ("setup", "sql"),
    [
        ("deny", "UPDATE risk_decision SET outcome = 'ALLOW' WHERE decision_id = $1"),
        ("deny", "UPDATE risk_decision SET subject_fingerprint = repeat('f', 64), "
                 "inputs_hash = repeat('f', 64) WHERE decision_id = $1"),
        ("expired", "UPDATE risk_decision SET expires_at = now() + interval '1 hour' "
                    "WHERE decision_id = $1"),
        ("other_tenant", "UPDATE risk_decision SET tenant_id = $2 WHERE decision_id = $1"),
        ("deny", "DELETE FROM risk_decision WHERE decision_id = $1"),
    ],
    ids=["flip_outcome", "rewrite_hashes", "extend_expiry", "steal_tenant", "delete_row"],
)
async def test_rsk006_worm_rejects_rewriting_decision_then_submit_still_fails(
    pool, victim, decision_repo, setup, sql
):
    ref = f"exec:{victim.execution_id}"
    owner = victim.user_id if setup != "other_tenant" else await create_test_user(pool)
    outcome = RiskOutcome.DENY if setup == "deny" else RiskOutcome.ALLOW
    ttl = timedelta(seconds=-1) if setup == "expired" else timedelta(minutes=5)
    target = await insert_decision(pool, owner, outcome=outcome, ttl=ttl, execution_ref=ref)
    before = await _snapshot(decision_repo, target.decision_id)
    assert before is not None

    args: tuple[object, ...] = (target.decision_id,)
    if "$2" in sql:
        args = (target.decision_id, victim.user_id)
    await _tamper(pool, sql, *args)

    assert await _snapshot(decision_repo, target.decision_id) == before  # WORM 행 불변
    # 타 tenant 행은 claim 전 WORM 재조회(tenant 스코프)가 먼저 거부하고, 같은
    # tenant의 DENY/만료 행은 결속(ref·intent·F0)이 맞으므로 트리거가 거부한다.
    expected = (RiskDecisionIntegrityError,) if setup == "other_tenant" else _TRIGGER_ERRORS
    await _attack(pool, victim, _forged_gate(target.decision_id, victim.f0), expected)


# --- 대조군: 진짜 ALLOW는 같은 경로로 통과한다 ------------------------------


async def test_control_genuine_allow_passes_the_same_path(pool, victim, decision_repo):
    """위 negative들이 '경로가 원래 막혀 있어서' 통과한 것이 아님을 증명한다."""
    ref = f"exec:{victim.execution_id}"
    genuine = await insert_decision(pool, victim.user_id, execution_ref=ref)
    adapter = RecordingAdapter()
    submitted = await submit_with_fence(
        pool, adapter, make_order(victim.execution_id), user_id=victim.user_id,
        gate_decision=_forged_gate(genuine.decision_id, victim.f0),
        read_fences=fence_reader(pool, victim.user_id, victim.execution_id),
        decision_reader=PostgresDecisionRepository(pool),
    )
    assert adapter.place_order_call_count == 1
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT risk_decision_id FROM orders WHERE order_id = $1", submitted.order_id
        )
    assert row is not None and row["risk_decision_id"] == genuine.decision_id
