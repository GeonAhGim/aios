"""VerifyAuditChain — AUD-003 운영 도구(관리자 전용, 아직 API 미배선).

Spec: AIOSproject 79번 §4 SLI "chain verification success".
"""
from __future__ import annotations

from uuid import UUID

from src.foundation.evidence.domain.rules import verify_chain
from src.foundation.evidence.ports.repository import AuditEventRepository


async def verify_audit_chain(repo: AuditEventRepository, tenant_id: UUID | None) -> None:
    """문제 없으면 조용히 반환. 깨졌으면 ChainIntegrityError를 그대로
    전파한다 — 79번 §4 "Alerts: checkpoint mismatch"로 이어질 신호이므로
    호출부가 삼키지 않는다."""
    events = await repo.list_chain_for_verification(tenant_id)
    verify_chain(events)
