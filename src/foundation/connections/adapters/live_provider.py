"""LiveReadonlyAccountProvider — live exchange adapter (legacy ``CredentialResolver``)

Read-only ``ReadonlyAccountProvider`` implementation on top of the legacy credential
system.

Spec: AIOSproject #74 §3, full audit (agent-platform-12, docs/FULL_AUDIT_2026-09-02.md
§6).

Reuses the existing credential system — FND-05's own vault (§3 "acquire short-lived
vault lease") does not yet handle real secrets. The reason
``confirm_connection.py`` creates only a placeholder via
``SecretLease(lease_ref=f"lease-{uuid4().hex}")`` is that the real API key is
already held by the legacy ``CredentialResolver`` (sec. 12.4, ``exchange_credentials``
table), so instead of creating a new vault we reuse the existing one
(`src/api/routers/foundation/validation.py` already has a precedent for linking
FND-04 to legacy credentials in the same way).

Scope verification limitation — same constraint documented in
``exchange_credential_service.py``: neither the Bitget nor KIS adapter implements an
endpoint to query API-key permission scopes (READ_BALANCE, etc. in AIOS's
classification) (FD-13.3 "If unsupported, substitute with a warning; do not claim
it verified"). This adapter follows the same principle —
``verify_readonly_scope()`` treats the requested scopes as approved without blocking
the connection itself, but honestly records
``ScopeProof.provider_verified=False`` ("this was not independently verified by the
provider").

Positions — both Bitget and KIS adapters are spot-only; ``get_positions()`` is
already documented to always return an empty list (see ``account_mixin.py``,
"the exchange does not know AIOS's strategy-level context"). Nevertheless, this
adapter does not skip the call based on that fact — it calls anyway and merges the
result. If other exchanges or contract types start populating real values later,
this code will need no changes.
"""
from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID

from src.foundation.connections.domain.models import (
    CapabilityScope,
    ProviderSnapshot,
    ScopeProof,
    SnapshotValue,
)
from src.foundation.connections.ports.provider import OpaqueRef, SecretLease
from src.services.credential_resolver import CredentialResolver

_CURRENCY_FIELD_MAX_LEN = 10  # account_snapshot.currency VARCHAR(10)
_NO_BALANCE_CURRENCY = "MULTI"


class LiveReadonlyAccountProvider:
    def __init__(
        self,
        resolver: CredentialResolver,
        *,
        user_id: UUID,
        exchange: str,
        requested_capability_profile: tuple[CapabilityScope, ...],
    ) -> None:
        self._resolver = resolver
        self._user_id = user_id
        self._exchange = exchange
        self._requested_capability_profile = requested_capability_profile

    async def verify_readonly_scope(self, lease: SecretLease) -> ScopeProof:
        # If get_adapter() raises CredentialNotFoundError (not registered / revoked),
        # propagate it — confirm_connection.py already wraps all exceptions in
        # ScopeVerificationFailedError. The get_balance() call itself is the only
        # and sufficient verification that "this key actually works".
        adapter = await self._resolver.get_adapter(self._user_id, self._exchange)
        await adapter.get_balance()
        return ScopeProof(
            granted_scopes=self._requested_capability_profile,
            provider_credential_ref=f"live:{self._exchange}:{self._user_id}",
            provider_verified=False,
        )

    async def fetch_snapshot(self, account_ref: OpaqueRef, as_of: datetime) -> ProviderSnapshot:
        adapter = await self._resolver.get_adapter(self._user_id, self._exchange)
        balances = await adapter.get_balance()
        positions = await adapter.get_positions()
        values = tuple(
            SnapshotValue(entity_type="BALANCE", entity_key=b.asset, value=b.total)
            for b in balances
        ) + tuple(
            SnapshotValue(entity_type="POSITION", entity_key=p.symbol, value=p.quantity)
            for p in positions
        )
        currency = balances[0].asset[:_CURRENCY_FIELD_MAX_LEN] if balances else _NO_BALANCE_CURRENCY
        return ProviderSnapshot(
            provider_as_of=datetime.now(timezone.utc),
            currency=currency,
            raw_payload_ref=f"live:{self._exchange}:{account_ref.value}:{as_of.isoformat()}",
            values=values,
        )
