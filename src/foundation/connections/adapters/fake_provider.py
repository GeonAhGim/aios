"""74th §7 rollout gate step 1 "fake provider" — real provider integration is out of scope for
this leaf (task-71 §6, task-74 §7 "This specification does not authorize trading
integration"). Test-only adapter; the only adapter until the real provider is approved.
"""
from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

from src.foundation.connections.domain.models import (
    CapabilityScope,
    ProviderSnapshot,
    ScopeProof,
    SnapshotValue,
)
from src.foundation.connections.ports.provider import OpaqueRef, SecretLease


class FakeReadonlyAccountProvider:
    """Fix `granted_scopes` at construction time to make scope drift (CON-002 series) tests
    deterministic — default is the happy path that approves all requested scopes."""

    def __init__(
        self,
        *,
        granted_scopes: tuple[CapabilityScope, ...] = (
            CapabilityScope.READ_BALANCE,
            CapabilityScope.READ_POSITION,
            CapabilityScope.READ_ACTIVITY,
        ),
        fail_verification: bool = False,
        fail_fetch: bool = False,
        snapshot_values: tuple[SnapshotValue, ...] = (),
    ) -> None:
        self._granted_scopes = granted_scopes
        self._fail_verification = fail_verification
        self._fail_fetch = fail_fetch
        self._snapshot_values = snapshot_values

    async def verify_readonly_scope(self, lease: SecretLease) -> ScopeProof:
        if self._fail_verification:
            raise ConnectionError("fake provider: scope verification 실패(시뮬레이션)")
        return ScopeProof(
            granted_scopes=self._granted_scopes,
            provider_credential_ref=f"fake-cred-{uuid4().hex[:8]}",
            provider_verified=True,
        )

    async def fetch_snapshot(self, account_ref: OpaqueRef, as_of: datetime) -> ProviderSnapshot:
        if self._fail_fetch:
            raise ConnectionError("fake provider: snapshot fetch 실패(시뮬레이션)")
        return ProviderSnapshot(
            provider_as_of=datetime.now(timezone.utc),
            currency="USD",
            raw_payload_ref=f"fake-payload-{uuid4().hex[:8]}",
            values=self._snapshot_values,
        )
