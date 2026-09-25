"""task-4698: `src/foundation/reconciliation/ports/repository.py` — 커버리지 0% 보강.

Spec: docs/specs/L4_reconciliation_resilience_v1.0.md, 80 §2/§4.

`@runtime_checkable` Protocol의 `isinstance()`는 메서드 **이름**만 확인한다 —
파라미터·반환 타입은 mypy(정적)가 확인한다(`tests/foundation/unit/evidence/
ports/test_ports_protocol.py`와 같은 패턴). negative test는 메서드 하나가 빠진
구현마다 isinstance()가 fail-closed로 False를 반환하는지 증명한다. 별도로
`ReconciliationRepository`를 직접 서브클래싱해 오버라이드하지 않은 메서드를
호출하면 Protocol 본문의 `...`/docstring 문이 실행되는 것(coverage 목적)과,
어댑터가 예외를 던지면 Protocol이 그 예외를 삼키지 않고 그대로 전파하는 것
(실패주입)을 함께 검증한다.
"""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from uuid import uuid4

import pytest

from src.foundation.reconciliation.domain.models import (
    Classification,
    ReconciliationItem,
    ReconciliationRun,
    ReconciliationState,
    RunState,
)
from src.foundation.reconciliation.ports.repository import ReconciliationRepository


class _FullReconciliationRepo:
    async def get_run_by_input_hash(self, target_ref, input_hash): ...
    async def insert_run_with_items(self, run, items): ...
    async def get_state(self, target_ref): ...
    async def list_states(self, tenant_id): ...
    async def upsert_state(self, state): ...
    async def transition_state_status(
        self,
        target_ref,
        *,
        expected_revision,
        new_status,
        blocking_reason,
        resolved_by=None,
        resolution_reason=None,
    ): ...


class _MissingGetRunByInputHashRepo:
    """`get_run_by_input_hash`가 빠진 불완전 구현 — 포트를 만족하지 못해야 한다."""

    async def insert_run_with_items(self, run, items): ...
    async def get_state(self, target_ref): ...
    async def list_states(self, tenant_id): ...
    async def upsert_state(self, state): ...
    async def transition_state_status(
        self,
        target_ref,
        *,
        expected_revision,
        new_status,
        blocking_reason,
        resolved_by=None,
        resolution_reason=None,
    ): ...


class _MissingInsertRunWithItemsRepo:
    """`insert_run_with_items`가 빠진 불완전 구현 — 포트를 만족하지 못해야 한다."""

    async def get_run_by_input_hash(self, target_ref, input_hash): ...
    async def get_state(self, target_ref): ...
    async def list_states(self, tenant_id): ...
    async def upsert_state(self, state): ...
    async def transition_state_status(
        self,
        target_ref,
        *,
        expected_revision,
        new_status,
        blocking_reason,
        resolved_by=None,
        resolution_reason=None,
    ): ...


class _MissingGetStateRepo:
    """`get_state`가 빠진 불완전 구현 — 포트를 만족하지 못해야 한다."""

    async def get_run_by_input_hash(self, target_ref, input_hash): ...
    async def insert_run_with_items(self, run, items): ...
    async def list_states(self, tenant_id): ...
    async def upsert_state(self, state): ...
    async def transition_state_status(
        self,
        target_ref,
        *,
        expected_revision,
        new_status,
        blocking_reason,
        resolved_by=None,
        resolution_reason=None,
    ): ...


class _MissingListStatesRepo:
    """`list_states`가 빠진 불완전 구현 — 포트를 만족하지 못해야 한다."""

    async def get_run_by_input_hash(self, target_ref, input_hash): ...
    async def insert_run_with_items(self, run, items): ...
    async def get_state(self, target_ref): ...
    async def upsert_state(self, state): ...
    async def transition_state_status(
        self,
        target_ref,
        *,
        expected_revision,
        new_status,
        blocking_reason,
        resolved_by=None,
        resolution_reason=None,
    ): ...


class _MissingUpsertStateRepo:
    """`upsert_state`가 빠진 불완전 구현 — 포트를 만족하지 못해야 한다."""

    async def get_run_by_input_hash(self, target_ref, input_hash): ...
    async def insert_run_with_items(self, run, items): ...
    async def get_state(self, target_ref): ...
    async def list_states(self, tenant_id): ...
    async def transition_state_status(
        self,
        target_ref,
        *,
        expected_revision,
        new_status,
        blocking_reason,
        resolved_by=None,
        resolution_reason=None,
    ): ...


class _MissingTransitionStateStatusRepo:
    """`transition_state_status`가 빠진 불완전 구현 — 포트를 만족하지 못해야 한다."""

    async def get_run_by_input_hash(self, target_ref, input_hash): ...
    async def insert_run_with_items(self, run, items): ...
    async def get_state(self, target_ref): ...
    async def list_states(self, tenant_id): ...
    async def upsert_state(self, state): ...


class _StubSubclass(ReconciliationRepository):
    """오버라이드 없이 Protocol 본문(`...`/docstring)을 그대로 물려받는
    서브클래스 — 본문 statement를 실행해 coverage를 채운다."""


class _RaisingUpsertStateRepo(ReconciliationRepository):
    """동시 갱신 충돌(revision 불일치)을 흉내내는 실패주입용 구현."""

    async def upsert_state(self, state):
        raise RuntimeError("conditional update conflict: revision mismatch")


def _make_state() -> ReconciliationState:
    return ReconciliationState(
        target_ref=uuid4(),
        target_type="mandate",
        tenant_id=uuid4(),
        aggregate_status=Classification.HEALTHY,
        last_healthy_at=datetime.now(timezone.utc),
        last_checked_at=datetime.now(timezone.utc),
        blocking_reason=None,
        revision=1,
        safety_control_id=None,
    )


def _make_run() -> ReconciliationRun:
    return ReconciliationRun(
        id=uuid4(),
        tenant_id=uuid4(),
        target_type="mandate",
        target_ref=uuid4(),
        connection_id=None,
        input_hash="deadbeef",
        state=RunState.COMPLETED,
        rule_version="v1",
    )


def test_full_implementation_satisfies_protocol() -> None:
    assert isinstance(_FullReconciliationRepo(), ReconciliationRepository)


def test_missing_get_run_by_input_hash_fails_port_check() -> None:
    assert not isinstance(_MissingGetRunByInputHashRepo(), ReconciliationRepository)


def test_missing_insert_run_with_items_fails_port_check() -> None:
    assert not isinstance(_MissingInsertRunWithItemsRepo(), ReconciliationRepository)


def test_missing_get_state_fails_port_check() -> None:
    assert not isinstance(_MissingGetStateRepo(), ReconciliationRepository)


def test_missing_list_states_fails_port_check() -> None:
    assert not isinstance(_MissingListStatesRepo(), ReconciliationRepository)


def test_missing_upsert_state_fails_port_check() -> None:
    assert not isinstance(_MissingUpsertStateRepo(), ReconciliationRepository)


def test_missing_transition_state_status_fails_port_check() -> None:
    assert not isinstance(_MissingTransitionStateStatusRepo(), ReconciliationRepository)


def test_empty_object_fails_port_check() -> None:
    """메서드가 전혀 없는 구현 — fail-closed 경계값."""
    assert not isinstance(object(), ReconciliationRepository)


async def test_default_stub_bodies_are_inert_when_invoked_directly() -> None:
    """`...`/docstring 본문을 오버라이드 없이 직접 호출하면 `None`을 반환할
    뿐, 예외를 던지지도 데이터를 만들어내지도 않는다 — 실제 구현은 반드시
    adapters/에서 와야 한다는 계약을 실증한다."""
    stub = _StubSubclass()
    run = _make_run()
    state = _make_state()

    assert await stub.get_run_by_input_hash(uuid4(), "deadbeef") is None
    assert await stub.insert_run_with_items(run, ()) is None
    assert await stub.get_state(uuid4()) is None
    assert await stub.list_states(uuid4()) is None
    assert await stub.upsert_state(state) is None
    assert (
        await stub.transition_state_status(
            uuid4(),
            expected_revision=1,
            new_status=Classification.RESOLVED,
            blocking_reason=None,
        )
        is None
    )


async def test_upsert_state_failure_propagates_uncaught() -> None:
    """어댑터가 revision 충돌로 예외를 던지면 포트는 그것을 삼키지 않는다."""
    repo = _RaisingUpsertStateRepo()

    with pytest.raises(RuntimeError, match="conditional update conflict"):
        await repo.upsert_state(_make_state())


async def test_transition_state_status_accepts_none_amount_edge_case() -> None:
    """`resolved_by`/`resolution_reason`은 옵션 필드 — `None` 경계값도
    본문 실행을 막지 않는다 (Decimal 값과 무관한 상태 전이 경로 확인)."""
    stub = _StubSubclass()

    result = await stub.transition_state_status(
        uuid4(),
        expected_revision=0,
        new_status=Classification.INVESTIGATING,
        blocking_reason="provider timeout",
        resolved_by=None,
        resolution_reason=None,
    )

    assert result is None


def test_reconciliation_item_uses_decimal_for_monetary_values() -> None:
    """CLAUDE.md 3절 — 금액은 항상 Decimal, float 금지(경계값 확인용 fixture)."""
    item = ReconciliationItem(
        id=uuid4(),
        run_id=uuid4(),
        entity_type="position",
        entity_key="BTC-USDT",
        internal_value=Decimal("100.5"),
        provider_value=None,
        classification=Classification.PENDING,
    )

    assert isinstance(item.internal_value, Decimal)
    assert item.provider_value is None
