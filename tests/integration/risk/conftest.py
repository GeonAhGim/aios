"""`evaluate_pre_submit` 통합테스트 공용 fixture/fake — R-35, task-9224.

`test_pre_submit_gate.py`(4-rule 기본 시나리오), `test_pre_submit_gate_
concurrency.py`(D3 동시성), `test_pre_submit_gate_recon_mismatch.py`
(RECON_MISMATCH 심볼단위 DENY, task-9224)가 공유한다 -- 파일마다 같은
fixture를 복제하면 조회 조건(예: `_RiskRepoWithFixedSafetyState`)이 서로
갈릴 위험이 있어 한 곳에 둔다(loc_over_500 분리와도 겸함, CLAUDE.md §7).
"""

from __future__ import annotations

import os
from datetime import datetime, timezone
from decimal import Decimal
from uuid import UUID, uuid4

import asyncpg
import pytest

from src.foundation.connections.domain.models import (
    AccountConnection,
    ConnectionHealth,
    ConnectionState,
    HealthState,
)
from src.foundation.risk_gate.adapters.postgres_decision_repository import (
    PostgresDecisionRepository,
)
from src.foundation.risk_gate.adapters.postgres_repository import PostgresRiskGateRepository
from src.services.risk_decision_recorder import RiskDecisionRecorder
from tests.integration.conftest import NoopEventBus

PROVIDER = "bitget"
SYMBOL = "BTC/USDT"
SIDE = "BUY"
QTY = Decimal("0.01")


class FakeConnectionRepo:
    """provider_code별 connection 유무·health만 흉내내는 최소 fake — 실제
    connection lifecycle 없이 freshness 전달 경로만 검증한다
    (test_risk_gate_lifecycle.py의 `_FakeHealthyConnectionRepo`와 동일 취지)."""

    def __init__(self, *, tenant_id: UUID, provider_code: str, health: HealthState | None) -> None:
        self._tenant_id = tenant_id
        self._provider_code = provider_code
        self._health = health
        self._connection_id = uuid4()

    async def list_connections(self, tenant_id: UUID) -> list[AccountConnection]:
        if tenant_id != self._tenant_id:
            return []
        return [
            AccountConnection(
                id=self._connection_id,
                tenant_id=self._tenant_id,
                owner_subject_id=self._tenant_id,
                provider_code=self._provider_code,
                opaque_account_ref="ACCT-TEST",
                state=ConnectionState.ACTIVE_READONLY,
                capability_profile=(),
                revision=1,
            )
        ]

    async def get_latest_health(self, connection_id: UUID) -> ConnectionHealth | None:
        if self._health is None:
            return None
        return ConnectionHealth(
            connection_id=connection_id, evaluated_at=datetime.now(timezone.utc), state=self._health
        )


class NoConnectionRepo:
    """이 provider에 connection 자체가 없는 경우 — connection_fresh=None."""

    async def list_connections(self, tenant_id: UUID) -> list[AccountConnection]:
        return []

    async def get_latest_health(self, connection_id: UUID) -> ConnectionHealth | None:
        return None


class NoOpenSignalsRepo:
    """RECON_MISMATCH가 아닌 시나리오가 쓰는 fake -- 항상 열린 신호가 없다고
    답해 기존 4-rule 시나리오를 그대로 유지한다(실제 RECON_MISMATCH DENY
    시나리오는 test_pre_submit_gate_recon_mismatch.py 참고)."""

    async def has_open_signal(
        self,
        *,
        tenant_id: UUID,
        signal_type,  # noqa: ANN001
        scope_ref: str,
    ) -> bool:
        return False


class RiskRepoWithFixedSafetyState:
    """fence/control은 실 DB(`PostgresRiskGateRepository`)에 그대로 위임하고
    circuit breaker/distrust level만 테스트가 원하는 값으로 고정한다 —
    `system_safety_state`는 프로세스 전역 단일 행이라 직접 UPDATE하면 같은
    DB를 공유하는 다른 테스트를 오염시킨다."""

    def __init__(
        self, inner: PostgresRiskGateRepository, *, cb_level: str | None, distrust_level: str | None
    ) -> None:
        self._inner = inner
        self._cb_level = cb_level
        self._distrust_level = distrust_level

    async def read_fence_and_controls(self, pairs):  # noqa: ANN001, ANN201
        return await self._inner.read_fence_and_controls(pairs)

    async def read_safety_state(self, *, provider_code: str, symbol: str):  # noqa: ANN201
        return self._cb_level, self._distrust_level


def _asyncpg_dsn() -> str:
    return os.environ["DATABASE_URL"].replace("postgresql+asyncpg://", "postgresql://")


@pytest.fixture
async def pool():
    p = await asyncpg.create_pool(_asyncpg_dsn(), min_size=2, max_size=8)
    yield p
    await p.close()


@pytest.fixture
def risk_repo(pool: asyncpg.Pool) -> PostgresRiskGateRepository:
    return PostgresRiskGateRepository(pool)


@pytest.fixture
def decision_repo(pool: asyncpg.Pool) -> PostgresDecisionRepository:
    return PostgresDecisionRepository(pool)


@pytest.fixture
def recorder(pool: asyncpg.Pool, decision_repo: PostgresDecisionRepository) -> RiskDecisionRecorder:
    return RiskDecisionRecorder(pool, decision_repo, NoopEventBus())


@pytest.fixture
def signal_repo() -> NoOpenSignalsRepo:
    return NoOpenSignalsRepo()


def healthy_connection_repo(tenant_id: UUID) -> FakeConnectionRepo:
    return FakeConnectionRepo(
        tenant_id=tenant_id, provider_code=PROVIDER, health=HealthState.HEALTHY
    )


def normal_risk_repo(risk_repo: PostgresRiskGateRepository) -> RiskRepoWithFixedSafetyState:
    return RiskRepoWithFixedSafetyState(risk_repo, cb_level="normal", distrust_level="NORMAL")
