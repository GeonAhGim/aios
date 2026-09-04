"""R-25 `RiskDecisionRecorder` 통합테스트 — 실 DB(`TEST_DATABASE_URL`) 대상.

Spec: docs/specs/L4_risk_and_safety_v1.0.md §9 R-25.
DoD: (1) insert 1행 + audit_log 1행 + `risk.decision.recorded` 1건이 한 경로에서
발생, (2) 시계 드리프트 > 2초는 DENY(`RISK_INPUT_STALE`)로 기록 + 로그, 2초
이내는 정상 통과(경계 양쪽), (3) PK 충돌은 재시도 없이 예외 전파, (4) 로그·
audit_log에 잔고 원값·inputs_snapshot 전문이 실리지 않음, (5) 롤백된(=저장
실패한) 결정은 이벤트를 남기지 않는다.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

import asyncpg
import pytest
from dotenv import dotenv_values

from src.core.risk.decision import GateKind, RiskDecision, RiskOutcome
from src.core.risk.inputs import (
    ActivityInputs,
    EquityInputs,
    ExposureSnapshot,
    OrderIntent,
    RiskInputs,
    SafetyInputs,
    StatsInputs,
)
from src.foundation.risk_gate.adapters.postgres_decision_repository import (
    PostgresDecisionRepository,
)
from src.services.risk_decision_recorder import (
    TOPIC_DECISION_RECORDED,
    TOPIC_LIMIT_BREACHED,
    RiskDecisionRecorder,
)
from tests.integration.conftest import NoopEventBus, create_test_user

_DISTINCTIVE_BALANCE = "919283746.55"


def _asyncpg_dsn() -> str:
    env = dotenv_values(Path(__file__).resolve().parents[3] / ".env")
    url = env.get("DATABASE_URL")
    assert url, ".env에 DATABASE_URL이 없습니다"
    return url.replace("postgresql+asyncpg://", "postgresql://")


@pytest.fixture
async def pool():
    p = await asyncpg.create_pool(_asyncpg_dsn(), min_size=1, max_size=4)
    yield p
    await p.close()


@pytest.fixture
def decision_repo(pool: asyncpg.Pool) -> PostgresDecisionRepository:
    return PostgresDecisionRepository(pool)


@pytest.fixture
def event_bus() -> NoopEventBus:
    return NoopEventBus()


@pytest.fixture
def recorder(
    pool: asyncpg.Pool, decision_repo: PostgresDecisionRepository, event_bus: NoopEventBus
) -> RiskDecisionRecorder:
    return RiskDecisionRecorder(pool, decision_repo, event_bus)


def _decision(*, tenant_id: object, evaluated_at: datetime, **overrides: object) -> RiskDecision:
    base: dict[str, object] = dict(
        decision_id=uuid4(),
        gate_kind=GateKind.PRE_TRADE,
        tenant_id=tenant_id,
        execution_ref="exec:1",
        subject_fingerprint="a" * 64,
        outcome=RiskOutcome.ALLOW,
        reason_codes=(),
        obligations=(),
        rule_results=(),
        rule_version="2026.09.1",
        rule_hash="b" * 64,
        engine_version="risk-engine/2",
        inputs_hash="c" * 64,
        input_refs=(),
        evaluated_at=evaluated_at,
        expires_at=evaluated_at + timedelta(minutes=5),
        trace_id=uuid4(),
        evidence_ref=None,
        latency_us=100,
    )
    base.update(overrides)
    return RiskDecision(**base)  # type: ignore[arg-type]


def _inputs(*, tenant_id: object, balance: Decimal = Decimal(_DISTINCTIVE_BALANCE)) -> RiskInputs:
    now = datetime(2026, 9, 4, tzinfo=timezone.utc)
    return RiskInputs(
        tenant_id=tenant_id,  # type: ignore[arg-type]
        execution_ref="exec:1",
        certified_badge=True,
        allocated_capital=Decimal("1000"),
        intent=OrderIntent(
            symbol="BTC/USDT",
            asset_class="CRYPTO_SPOT",
            side="BUY",
            quantity=Decimal("0.1"),
            ref_price=Decimal("50000"),
            notional=Decimal("5000"),
            reduce_only=False,
            strategy_id="strat-1",
            strategy_version="1.0",
            capital_pct=Decimal("10"),
        ),
        equity=EquityInputs(total_equity=balance, as_of=now),
        exposure=ExposureSnapshot(position_quantity=Decimal("0"), as_of=now),
        stats=StatsInputs(as_of=now),
        activity=ActivityInputs(),
        safety=SafetyInputs(circuit_breaker_level="normal"),
        limits=(),
        as_of=now,
    )


async def _server_now(pool: asyncpg.Pool) -> datetime:
    async with pool.acquire() as conn:
        value: datetime = await conn.fetchval("SELECT now()")
    return value


async def _audit_rows(pool: asyncpg.Pool, decision_id: object) -> list[asyncpg.Record]:
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT * FROM audit_log WHERE target_type = 'risk_decision' AND target_id = $1",
            str(decision_id),
        )
    return list(rows)


async def test_record_writes_decision_and_audit_log_and_publishes_event(
    pool: asyncpg.Pool,
    decision_repo: PostgresDecisionRepository,
    recorder: RiskDecisionRecorder,
    event_bus: NoopEventBus,
) -> None:
    tenant_id = await create_test_user(pool)
    now = await _server_now(pool)
    decision = _decision(tenant_id=tenant_id, evaluated_at=now)

    await recorder.record(decision, _inputs(tenant_id=tenant_id), actor="risk-engine")

    stored = await decision_repo.get(decision.decision_id)
    assert stored is not None
    assert stored[0].outcome == RiskOutcome.ALLOW

    audit_rows = await _audit_rows(pool, decision.decision_id)
    assert len(audit_rows) == 1
    assert audit_rows[0]["action_type"] == "risk_decision_recorded"
    assert audit_rows[0]["user_id"] == tenant_id

    assert len(event_bus.published) == 1
    topic, payload = event_bus.published[0]
    assert topic == TOPIC_DECISION_RECORDED
    assert payload["decision_id"] == str(decision.decision_id)
    assert payload["outcome"] == "ALLOW"


async def test_clock_skew_beyond_tolerance_forces_deny_and_logs(
    pool: asyncpg.Pool,
    decision_repo: PostgresDecisionRepository,
    recorder: RiskDecisionRecorder,
    caplog: pytest.LogCaptureFixture,
) -> None:
    tenant_id = await create_test_user(pool)
    now = await _server_now(pool)
    stale_decision = _decision(tenant_id=tenant_id, evaluated_at=now - timedelta(seconds=2.2))

    with caplog.at_level("WARNING"):
        await recorder.record(stale_decision, _inputs(tenant_id=tenant_id), actor="risk-engine")

    stored = await decision_repo.get(stale_decision.decision_id)
    assert stored is not None
    assert stored[0].outcome == RiskOutcome.DENY
    assert "RISK_INPUT_STALE" in stored[0].reason_codes
    assert any("clock_skew_detected" in record.message for record in caplog.records)


async def test_clock_skew_within_tolerance_passes_through_unmodified(
    pool: asyncpg.Pool,
    decision_repo: PostgresDecisionRepository,
    recorder: RiskDecisionRecorder,
) -> None:
    tenant_id = await create_test_user(pool)
    now = await _server_now(pool)
    fresh_decision = _decision(tenant_id=tenant_id, evaluated_at=now - timedelta(seconds=1.8))

    await recorder.record(fresh_decision, _inputs(tenant_id=tenant_id), actor="risk-engine")

    stored = await decision_repo.get(fresh_decision.decision_id)
    assert stored is not None
    assert stored[0].outcome == RiskOutcome.ALLOW
    assert "RISK_INPUT_STALE" not in stored[0].reason_codes


async def test_duplicate_decision_id_propagates_without_retry_and_no_extra_event(
    pool: asyncpg.Pool,
    recorder: RiskDecisionRecorder,
    event_bus: NoopEventBus,
) -> None:
    tenant_id = await create_test_user(pool)
    now = await _server_now(pool)
    decision = _decision(tenant_id=tenant_id, evaluated_at=now)
    inputs = _inputs(tenant_id=tenant_id)

    await recorder.record(decision, inputs, actor="risk-engine")
    assert len(event_bus.published) == 1

    with pytest.raises(asyncpg.UniqueViolationError):
        await recorder.record(decision, inputs, actor="risk-engine")

    # 실패한(=저장되지 않은) 두 번째 시도는 감사·이벤트를 남기지 않는다.
    assert len(event_bus.published) == 1
    assert len(await _audit_rows(pool, decision.decision_id)) == 1


async def test_audit_log_excludes_raw_balance_and_inputs_snapshot(
    pool: asyncpg.Pool,
    recorder: RiskDecisionRecorder,
    event_bus: NoopEventBus,
) -> None:
    tenant_id = await create_test_user(pool)
    now = await _server_now(pool)
    decision = _decision(tenant_id=tenant_id, evaluated_at=now)

    await recorder.record(decision, _inputs(tenant_id=tenant_id), actor="risk-engine")

    audit_rows = await _audit_rows(pool, decision.decision_id)
    decision_data_raw = audit_rows[0]["decision_data"]
    decision_data = (
        json.loads(decision_data_raw) if isinstance(decision_data_raw, str) else decision_data_raw
    )
    serialized_audit = json.dumps(decision_data)
    assert _DISTINCTIVE_BALANCE not in serialized_audit
    assert "inputs_snapshot" not in decision_data
    assert "total_equity" not in serialized_audit

    _, event_payload = event_bus.published[0]
    assert _DISTINCTIVE_BALANCE not in json.dumps(event_payload)


async def test_limit_breach_reason_code_publishes_extra_event(
    pool: asyncpg.Pool,
    recorder: RiskDecisionRecorder,
    event_bus: NoopEventBus,
) -> None:
    tenant_id = await create_test_user(pool)
    now = await _server_now(pool)
    decision = _decision(
        tenant_id=tenant_id,
        evaluated_at=now,
        outcome=RiskOutcome.DENY,
        reason_codes=("RISK_LIMIT_BREACH:SYMBOL:GROSS_NOTIONAL_PCT",),
    )

    await recorder.record(decision, _inputs(tenant_id=tenant_id), actor="risk-engine")

    topics = [topic for topic, _ in event_bus.published]
    assert topics == [TOPIC_DECISION_RECORDED, TOPIC_LIMIT_BREACHED]
    breach_payload = event_bus.published[1][1]
    assert breach_payload["scope"] == "SYMBOL"
    assert breach_payload["metric"] == "GROSS_NOTIONAL_PCT"
