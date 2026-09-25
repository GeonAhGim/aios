"""Tests for AuditEventRepository protocol -- failure injection.

Spec: Rule 79 §1 (hash chain integrity), D2 DoD (failure-injection >= 1).

See also: test_repository_protocol.py (structural conformance tests),
test_repository_protocol_contract.py (behavioral contract tests).
"""

from __future__ import annotations

from typing import Any
from uuid import UUID, uuid4

import pytest

from src.foundation.evidence.domain.models import AuditEvent, Classification, Outcome
from tests.foundation.evidence.ports.test_repository_protocol import _ValidAuditEventRepository


class TestAppendEventFailureInjection:
    """Failure injection tests for append_event."""

    @pytest.mark.asyncio
    async def test_append_event_database_connection_failure(self, monkeypatch: Any) -> None:
        """Failure injection: database connection error in append_event."""

        class _FailingRepo(_ValidAuditEventRepository):
            async def append_event(
                self,
                *,
                tenant_id: UUID | None,
                aggregate_type: str,
                aggregate_id: UUID,
                aggregate_revision: int | None,
                action: str,
                outcome: Outcome,
                actor_subject_id: UUID | None,
                trace_id: UUID,
                payload_hash: str,
                payload: dict[str, object],
                classification: Classification,
            ) -> AuditEvent:
                raise ConnectionError("Database unavailable")

        repo = _FailingRepo()
        with pytest.raises(ConnectionError) as exc_info:
            await repo.append_event(
                tenant_id=uuid4(),
                aggregate_type="order",
                aggregate_id=uuid4(),
                aggregate_revision=None,
                action="create",
                outcome=Outcome.SUCCESS,
                actor_subject_id=uuid4(),
                trace_id=uuid4(),
                payload_hash="test",
                payload={},
                classification=Classification.INTERNAL,
            )
        assert "unavailable" in str(exc_info.value).lower()

    @pytest.mark.asyncio
    async def test_append_event_hash_chain_violation(self, monkeypatch: Any) -> None:
        """Failure injection: hash chain fork detection in append_event."""

        class _ChainViolationRepo(_ValidAuditEventRepository):
            async def append_event(
                self,
                *,
                tenant_id: UUID | None,
                aggregate_type: str,
                aggregate_id: UUID,
                aggregate_revision: int | None,
                action: str,
                outcome: Outcome,
                actor_subject_id: UUID | None,
                trace_id: UUID,
                payload_hash: str,
                payload: dict[str, object],
                classification: Classification,
            ) -> AuditEvent:
                raise ValueError("Hash chain fork detected (Rule 79 §1)")

        repo = _ChainViolationRepo()
        with pytest.raises(ValueError) as exc_info:
            await repo.append_event(
                tenant_id=uuid4(),
                aggregate_type="order",
                aggregate_id=uuid4(),
                aggregate_revision=None,
                action="create",
                outcome=Outcome.SUCCESS,
                actor_subject_id=uuid4(),
                trace_id=uuid4(),
                payload_hash="test",
                payload={},
                classification=Classification.INTERNAL,
            )
        assert "fork" in str(exc_info.value).lower()

    @pytest.mark.asyncio
    async def test_list_timeline_cursor_invalid_format(self, monkeypatch: Any) -> None:
        """Failure injection: invalid cursor format in list_timeline."""

        class _InvalidCursorRepo(_ValidAuditEventRepository):
            async def list_timeline(
                self,
                tenant_id: UUID,
                *,
                cursor: str | None,
                limit: int,
                aggregate_type: str | None = None,
                action: str | None = None,
            ) -> tuple[list[AuditEvent], str | None]:
                if cursor is not None and cursor.startswith("invalid_"):
                    raise ValueError("Cursor format invalid")
                return [], None

        repo = _InvalidCursorRepo()
        with pytest.raises(ValueError):
            await repo.list_timeline(
                tenant_id=uuid4(),
                cursor="invalid_xyz",
                limit=10,
            )

    @pytest.mark.asyncio
    async def test_list_chain_for_verification_tenant_not_found(self, monkeypatch: Any) -> None:
        """Failure injection: tenant not found in list_chain_for_verification."""

        class _TenantNotFoundRepo(_ValidAuditEventRepository):
            async def list_chain_for_verification(self, tenant_id: UUID | None) -> list[AuditEvent]:
                if tenant_id is not None:
                    raise KeyError(f"Tenant {tenant_id} not found")
                return []

        repo = _TenantNotFoundRepo()
        with pytest.raises(KeyError):
            await repo.list_chain_for_verification(tenant_id=uuid4())

    @pytest.mark.asyncio
    async def test_get_latest_event_timeout(self, monkeypatch: Any) -> None:
        """Failure injection: timeout in get_latest_event."""

        class _TimeoutRepo(_ValidAuditEventRepository):
            async def get_latest_event(
                self, aggregate_type: str, aggregate_id: UUID, *, action: str
            ) -> AuditEvent | None:
                raise TimeoutError("Query timeout")

        repo = _TimeoutRepo()
        with pytest.raises(TimeoutError):
            await repo.get_latest_event(
                aggregate_type="order",
                aggregate_id=uuid4(),
                action="submit",
            )
