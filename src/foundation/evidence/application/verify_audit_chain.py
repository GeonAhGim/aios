"""VerifyAuditChain — AUD-003 operational tool (admin-only, API not yet wired).

Spec: AIOSproject No. 79 §4 SLI "chain verification success".
"""
from __future__ import annotations

from uuid import UUID

from src.foundation.evidence.domain.rules import verify_chain
from src.foundation.evidence.ports.repository import AuditEventRepository


async def verify_audit_chain(repo: AuditEventRepository, tenant_id: UUID | None) -> None:
    """Return quietly if nothing is wrong. If the chain is broken, let
    ChainIntegrityError propagate — this signal feeds into 79 §4
    "Alerts: checkpoint mismatch", so the caller must not swallow it."""
    events = await repo.list_chain_for_verification(tenant_id)
    verify_chain(events)
