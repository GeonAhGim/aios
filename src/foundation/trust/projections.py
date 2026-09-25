"""Build TrustStatusView — 73 §5 GET /v1/trust/status.

71 §4 "read model may lag" — currently reads the same DB directly from the command
handler so there is no lag (single Postgres, no separate projection worker), but
always includes `as_of` so the interface can be swapped to an async projection later
(108 §2 field standard).
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
