"""Task-74 §3 ReadonlyAccountProvider — no order/transfer/withdrawal/signature/raw
credential lookup methods live on this Protocol (CON-009 pins it as a contract
test). Real provider integration follows task-71 §6 "decide after provider/legal
review" — this leaf only implements the fake adapter (adapters/fake_provider.py)."""
from __future__ import annotations

from datetime import datetime
from typing import Protocol

from src.foundation.connections.domain.models import ProviderSnapshot, ScopeProof


class SecretLease:
    """Handle for a short-lived credential borrowed from vault — the raw secret
    never leaves the provider adapter (task-74 §3 "acquire short-lived vault lease")."""

    def __init__(self, lease_ref: str) -> None:
        self.lease_ref = lease_ref


class OpaqueRef:
    def __init__(self, value: str) -> None:
        self.value = value


class ReadonlyAccountProvider(Protocol):
    async def verify_readonly_scope(self, lease: SecretLease) -> ScopeProof: ...

    async def fetch_snapshot(
        self, account_ref: OpaqueRef, as_of: datetime
    ) -> ProviderSnapshot: ...
