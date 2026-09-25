"""EO-02 — Execution ownership (lease) repository port.

Spec: docs/specs/L4_execution_ownership_and_safety_gate_wiring_v1.0.md
§2-A, §3.2. The domain knows only this Protocol; actual implementations
(in adapters/) remain unknown to it (§4)."""
from __future__ import annotations

from typing import Protocol


class ExecutionLeaseRepository(Protocol):
    async def acquire_or_renew_many(
        self, execution_ids: list[int], *, owner_id: str, ttl_seconds: float
    ) -> set[int]:
        """Return only the execution_ids for which a lease was acquired or
        already held (renewal succeeded). If another owner_id holds a valid
        lease (not yet expired), that execution_id is excluded from the
        return set (§4.1 — "tick runs under exactly one process holding a
        valid lease"). Renewal failure is signaled only by absence from the
        return set — no exception is raised; the caller simply skips that
        execution_id for this cycle."""
        ...

    async def release_all(self, owner_id: str) -> int:
        """Release (delete) all leases held by this owner_id and return the
        number of rows removed. On graceful shutdown (SIGTERM), this frees
        leases immediately so another process can acquire them without
        waiting for expiration (§6)."""
        ...
