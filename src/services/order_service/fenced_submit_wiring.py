"""task-1717 P0-D — `fenced_submit.submit_with_fence`가 요구하는 두 어댑터
(`FenceReader`/`DecisionReader`)를 조립부(`background_loops.py` 등)에서만
구성하기 위한 팩토리. `foundation_gate.py`와 같은 이유로 foundation을 여기서
직접 import한다 — `executor.py`/`fenced_submit.py`는 이 모듈이 돌려준
값(클로저·구조적 Protocol 구현체)만 받는다.
"""
from __future__ import annotations

from collections.abc import Callable, Mapping
from uuid import UUID

import asyncpg

from src.foundation.risk_gate.adapters.postgres_decision_repository import (
    PostgresDecisionRepository,
)
from src.foundation.risk_gate.adapters.postgres_repository import PostgresRiskGateRepository
from src.foundation.risk_gate.application.read_fence import read_fence_snapshot
from src.services.order_service.fenced_submit import FenceReader
from src.services.order_service.worm_decision_check import DecisionReader


def make_fence_reader_factory(
    pool: asyncpg.Pool,
) -> Callable[[UUID, str, str], FenceReader]:
    """무항 `FenceReader`를 주문마다(tenant/provider/execution_ref별) 새로
    만드는 팩토리."""
    risk_repo = PostgresRiskGateRepository(pool)

    def factory(tenant_id: UUID, provider_code: str, execution_ref: str) -> FenceReader:
        async def read() -> Mapping[str, int]:
            snapshot = await read_fence_snapshot(
                risk_repo,
                tenant_id=tenant_id,
                provider_code=provider_code,
                execution_ref=execution_ref,
            )
            return {
                f"{scope.value}:{ref}": token for (scope, ref), token in snapshot.tokens.items()
            }

        return read

    return factory


def make_decision_reader(pool: asyncpg.Pool) -> DecisionReader:
    """`PostgresDecisionRepository`는 `DecisionReader` Protocol을 구조적으로
    만족한다 — 별도 어댑터가 필요 없다."""
    return PostgresDecisionRepository(pool)
