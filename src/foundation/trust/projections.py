"""TrustStatusView 조립 — 73번 §5 GET /v1/trust/status.

71번 §4 "read model may lag" — 지금은 command handler와 같은 DB를 직접 읽으므로
지연이 없지만(단일 Postgres, 별도 프로젝션 워커 없음), 인터페이스는 나중에
비동기 프로젝션으로 교체 가능하게 `as_of`를 항상 포함한다(108번 §2 필드 표준).
"""
from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID

from src.foundation.trust.contracts.v1 import ConsentDecision, ConsentState
from src.foundation.trust.ports.repository import TrustRepository


class TrustStatusView:
    def __init__(self, tenant_id: UUID, consents: list[ConsentDecision], as_of: datetime) -> None:
        self.tenant_id = tenant_id
        self.consents = consents
        self.as_of = as_of


async def build_trust_status_view(repo: TrustRepository, tenant_id: UUID) -> TrustStatusView:
    consents = await repo.list_active_consents(tenant_id)
    return TrustStatusView(
        tenant_id=tenant_id,
        consents=[
            ConsentDecision(
                consent_id=c.id,
                tenant_id=c.tenant_id,
                purpose=c.purpose,
                disclosure_id=c.disclosure_id,
                disclosure_revision=c.disclosure_revision,
                state=ConsentState(c.state.value),
                accepted_at=c.accepted_at,
                revoked_at=c.revoked_at,
                expires_at=c.expires_at,
            )
            for c in consents
        ],
        as_of=datetime.now(timezone.utc),
    )
