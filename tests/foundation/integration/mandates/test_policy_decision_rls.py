"""F2(task-12103, tier M) -- `insert_policy_decision`/`get_cached_decision`
actually bind the `app.tenant_id` GUC.

Audit reproduction (docs/audits/AUDIT_2026-10-15_compliance_approval.md F2):
`policy_decision` now carries RLS ENABLE+FORCE (`d3f8a6c91e52`) but, before
this leaf, `postgres_policy_repository.py`'s `insert_policy_decision`/
`get_cached_decision` only used a bare `self._pool.acquire()` -- never
`tenant_transaction()` -- exactly the gap F1(task-9454) already fixed for
`portfolio_mandate`. In this environment DATABASE_URL's role is still a
superuser (`rolbypassrls=true`), so the gap does not show up today; once
that role stops bypassing RLS, a legitimate tenant's own read/write would
silently see/insert zero rows.

Like `test_postgres_repository_rls.py`, this exercises the real
`PostgresMandateRepository` method call path and only lowers the role the
repository's own connection acquires to `aios_app` (`_AppRolePool`), rather
than hand-rolling SQL via `AppRoleTx`.
"""

from __future__ import annotations

import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from uuid import UUID, uuid4

import asyncpg
import pytest

from src.foundation.mandates.adapters.postgres_repository import PostgresMandateRepository
from src.foundation.mandates.application.activate_revision import (
    activate_revision as activate_revision_command,
)
from src.foundation.mandates.application.create_draft_mandate import create_draft_mandate
from src.foundation.mandates.application.evaluate_policy import evaluate as evaluate_policy_command
from src.foundation.mandates.contracts.v1 import PolicyEvaluationSubject
from src.foundation.mandates.domain.models import PolicyDecision, PolicyOutcome
from tests.foundation.integration.mandates.conftest import default_rules
from tests.integration.conftest import create_test_tenant

# Same borrowed-budget rationale as test_postgres_repository_rls.py's
# _RLS_SELECT_P95_BUDGET_MS.
_RLS_SELECT_P95_BUDGET_MS = 50.0


class _AppRolePool:
    """Wraps a real `pool`, lowering only the role of the connection
    `acquire()` hands out to `aios_app` -- see
    `test_postgres_repository_rls.py` for the full rationale (this is the
    same helper, duplicated to keep this file's import surface self
    contained rather than reaching across test files)."""

    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    @asynccontextmanager
    async def acquire(self) -> AsyncIterator[asyncpg.Connection]:
        async with self._pool.acquire() as conn:
            await conn.execute("SET ROLE aios_app")
            try:
                yield conn
            finally:
                await conn.execute("RESET ROLE")


async def _activated_tenant(
    pool: asyncpg.Pool, repo: PostgresMandateRepository, trust_repo
) -> UUID:
    tenant_id = await create_test_tenant(pool)
    draft = await create_draft_mandate(
        repo, tenant_id=tenant_id, subject_id=tenant_id, rules=default_rules()
    )
    await activate_revision_command(
        repo,
        trust_repo,
        tenant_id=tenant_id,
        subject_id=tenant_id,
        revision_id=draft.id,
        reauthenticated=False,
    )
    return tenant_id


async def _fingerprint_of(pool: asyncpg.Pool, decision_id: UUID) -> str:
    """`PolicyDecisionView` (contracts/v1.py) deliberately omits
    `command_fingerprint` -- it is a cache key, not an API field. Fetch it
    straight from the row via the superuser pool (test setup only, not the
    GUC-bound path under test)."""
    async with pool.acquire() as conn:
        value = await conn.fetchval(
            "SELECT command_fingerprint FROM policy_decision WHERE id = $1", decision_id
        )
    assert value is not None
    return str(value)


def _decision(tenant_id: UUID, bundle_id: UUID, fingerprint: str) -> PolicyDecision:
    return PolicyDecision(
        id=uuid4(),
        tenant_id=tenant_id,
        bundle_id=bundle_id,
        command_type="rls-test",
        command_fingerprint=fingerprint,
        outcome=PolicyOutcome.ALLOW,
        reason_codes=(),
        obligations=(),
        evaluated_at=datetime.now(UTC),
        expires_at=None,
    )


async def test_get_cached_decision_returns_row_when_tenant_guc_is_bound(
    pool, repo, trust_repo
) -> None:
    """Second half of the audit reproduction -- after the fix, a normal
    tenant's cached-decision read comes back as 1 row even under `aios_app`,
    because `get_cached_decision` now binds `app.tenant_id` itself."""
    tenant_a = await _activated_tenant(pool, repo, trust_repo)
    decision = await evaluate_policy_command(
        repo, tenant_id=tenant_a, subject=PolicyEvaluationSubject(command_type="x")
    )
    app_role_repo = PostgresMandateRepository(_AppRolePool(pool))

    found = await app_role_repo.get_cached_decision(
        tenant_a, await _fingerprint_of(pool, decision.id)
    )

    assert found is not None
    assert found.id == decision.id


async def test_get_cached_decision_cross_tenant_lookup_returns_nothing(
    pool, repo, trust_repo
) -> None:
    """negative (cross-tenant): tenant A guessing tenant B's command_fingerprint
    (e.g. by observing the same `command_type`) must not see B's decision --
    the WHERE clause and the `app.tenant_id` GUC both key off tenant A."""
    tenant_a = await _activated_tenant(pool, repo, trust_repo)
    tenant_b = await _activated_tenant(pool, repo, trust_repo)
    decision_b = await evaluate_policy_command(
        repo, tenant_id=tenant_b, subject=PolicyEvaluationSubject(command_type="shared-type")
    )
    app_role_repo = PostgresMandateRepository(_AppRolePool(pool))

    found = await app_role_repo.get_cached_decision(
        tenant_a, await _fingerprint_of(pool, decision_b.id)
    )

    assert found is None


async def test_get_cached_decision_fails_closed_when_guc_binding_is_skipped(
    monkeypatch: pytest.MonkeyPatch, pool, repo, trust_repo
) -> None:
    """negative + failure-injection: pins the pre-fix symptom. If
    `get_cached_decision` stopped calling `tenant_transaction` (reverted to a
    bare `pool.acquire()`), a normal tenant's own cached read must come back
    empty under `aios_app` -- fail-closed, not fail-open."""
    tenant_a = await _activated_tenant(pool, repo, trust_repo)
    decision = await evaluate_policy_command(
        repo, tenant_id=tenant_a, subject=PolicyEvaluationSubject(command_type="x")
    )
    fingerprint = await _fingerprint_of(pool, decision.id)

    @asynccontextmanager
    async def _unbound_tenant_transaction(
        pool_arg: asyncpg.Pool, _tenant_id: UUID | None
    ) -> AsyncIterator[asyncpg.Connection]:
        async with pool_arg.acquire() as conn, conn.transaction():
            yield conn

    monkeypatch.setattr(
        "src.foundation.mandates.adapters.postgres_policy_repository.tenant_transaction",
        _unbound_tenant_transaction,
    )
    app_role_repo = PostgresMandateRepository(_AppRolePool(pool))

    found = await app_role_repo.get_cached_decision(tenant_a, fingerprint)

    assert found is None


async def test_insert_policy_decision_fails_closed_when_guc_binding_is_skipped(
    monkeypatch: pytest.MonkeyPatch, pool, repo, trust_repo
) -> None:
    """negative + failure-injection (insert side): without GUC binding, the
    RLS `WITH CHECK` on `policy_decision` has nothing to compare `tenant_id`
    against, so the INSERT itself is rejected rather than silently landing
    under the wrong tenant (fail-closed on the write path too)."""
    tenant_a = await _activated_tenant(pool, repo, trust_repo)
    mandate = await repo.get_mandate(tenant_a)
    assert mandate is not None
    bundle = await repo.get_bundle_for_revision(mandate.active_revision_id)
    if bundle is None:
        decision = await evaluate_policy_command(
            repo, tenant_id=tenant_a, subject=PolicyEvaluationSubject(command_type="warm-up")
        )
        bundle_id = decision.bundle_id
    else:
        bundle_id = bundle.id

    @asynccontextmanager
    async def _unbound_tenant_transaction(
        pool_arg: asyncpg.Pool, _tenant_id: UUID | None
    ) -> AsyncIterator[asyncpg.Connection]:
        async with pool_arg.acquire() as conn, conn.transaction():
            yield conn

    monkeypatch.setattr(
        "src.foundation.mandates.adapters.postgres_policy_repository.tenant_transaction",
        _unbound_tenant_transaction,
    )
    app_role_repo = PostgresMandateRepository(_AppRolePool(pool))

    with pytest.raises(asyncpg.InsufficientPrivilegeError):
        await app_role_repo.insert_policy_decision(_decision(tenant_a, bundle_id, "unbound-insert"))


async def _get_cached_decision_p95_ms(
    app_role_repo: PostgresMandateRepository, tenant_id: UUID, fingerprint: str, *, n: int
) -> float:
    durations_ms: list[float] = []
    for _ in range(n):
        start = time.perf_counter()
        await app_role_repo.get_cached_decision(tenant_id, fingerprint)
        durations_ms.append((time.perf_counter() - start) * 1000)
    durations_ms.sort()
    return durations_ms[int(len(durations_ms) * 0.95)]


async def test_get_cached_decision_under_app_role_p95_under_borrowed_order_ack_budget(
    pool, repo, trust_repo
) -> None:
    """Numeric performance assertion: p95 of a single real-DB round trip
    (tenant_transaction + SET ROLE aios_app + RLS policy evaluation) over 30
    repeats, against the same borrowed budget as
    test_postgres_repository_rls.py."""
    tenant_a = await _activated_tenant(pool, repo, trust_repo)
    decision = await evaluate_policy_command(
        repo, tenant_id=tenant_a, subject=PolicyEvaluationSubject(command_type="x")
    )
    fingerprint = await _fingerprint_of(pool, decision.id)
    app_role_repo = PostgresMandateRepository(_AppRolePool(pool))

    p95_ms = await _get_cached_decision_p95_ms(app_role_repo, tenant_a, fingerprint, n=30)

    assert p95_ms < _RLS_SELECT_P95_BUDGET_MS
