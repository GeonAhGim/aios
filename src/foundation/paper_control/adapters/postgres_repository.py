"""Asyncpg implementation of PaperControlRepository.

Spec: AIOSproject #77 §2/§3, #105 (concurrency standard).

increment_fence() bundles state transition and fence increment into a single UPDATE —
conditional_update() in conditional_write.py only supports "SET to new value" and
does not support relative expressions like "current value + 1", so we write raw SQL here
(standard #105 §2.2 exception — this query already carries a `WHERE state = $2` guard,
so it fully satisfies the core principle of the standard).

AUDIT_2026-09-30_auth_rls.md F1 (task-9455) — `paper_deployment` is one of the 8
FORCE RLS tables (b3c7f19ad2e6/c9f4e2a1b6d7); the RLS policy is judged by the
`app.tenant_id` GUC, not by any tenant condition present in the SQL. Every method
below that already has a `tenant_id` in scope opens its connection via
[[tenant_transaction]] (same contract as connections/adapters/postgres_repository.py)
and keeps the explicit `tenant_id` WHERE/CAS condition as the first line of defense
while this environment's DATABASE_URL role remains a superuser (RLS is not applied to
superusers) — tenant_transaction() is the second line of defense for once the
production DSN switches to a non-superuser role (e.g. `aios_app`).
`get_deployment(deployment_id)`/`list_running_deployments()` are deliberately left on
plain `pool.acquire()`: both are called before any tenant_id is known (ownership is
verified by the *caller* only after the row comes back — see
`pause_deployment._load_owned_deployment`, `start_deployment._start_or_resume`,
`submit_paper_intent.submit_paper_intent`; `list_running_deployments()` is the
documented all-tenant GLOBAL kill-switch scan), so there is no tenant_id to bind a GUC
with at that call site (same rationale as `PostgresConnectionRepository.get_connection`).
`deployment_command`/`paper_order_intent` are child tables excluded from the RLS
rollout (parent FK isolation, b3c7f19ad2e6 docstring), so their methods
(`insert_command`/`get_command_by_idempotency_key`/`insert_order_intent`) are unaffected
by F1 and stay on `pool.acquire()`."""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID

import asyncpg

from src.core.db.conditional_write import ConcurrencyConflictError, conditional_update
from src.core.db.tenant_scope import tenant_transaction
from src.foundation.paper_control.domain.models import (
    AdapterProvenance,
    CommandOutcome,
    CommandType,
    CredentialClass,
    DeploymentCommand,
    DeploymentState,
    PaperDeployment,
    PaperOrderIntent,
)


def _row_to_deployment(row: asyncpg.Record) -> PaperDeployment:
    return PaperDeployment(
        id=row["id"],
        tenant_id=row["tenant_id"],
        connection_id=row["connection_id"],
        package_ref=row["package_ref"],
        mandate_revision_id=row["mandate_revision_id"],
        provenance=AdapterProvenance(
            adapter_type=row["adapter_type"],
            credential_class=CredentialClass(row["credential_class"]),
            endpoint_classification=row["endpoint_classification"],
            provider_sandbox_account_ref=row["provider_sandbox_account_ref"],
        ),
        state=DeploymentState(row["state"]),
        fence_token=row["fence_token"],
        request_idempotency_key=row["request_idempotency_key"],
        request_digest=row["request_digest"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def _row_to_command(row: asyncpg.Record) -> DeploymentCommand:
    return DeploymentCommand(
        id=row["id"],
        deployment_id=row["deployment_id"],
        idempotency_key=row["idempotency_key"],
        command_type=CommandType(row["command_type"]),
        actor_subject_id=row["actor_subject_id"],
        outcome=CommandOutcome(row["outcome"]),
        detail=row["detail"],
        created_at=row["created_at"],
    )


def _row_to_intent(row: asyncpg.Record) -> PaperOrderIntent:
    return PaperOrderIntent(
        id=row["id"],
        deployment_id=row["deployment_id"],
        sequence=row["sequence"],
        fence_token_at_submit=row["fence_token_at_submit"],
        state=row["state"],
        created_at=row["created_at"],
    )


class PostgresPaperControlRepository:
    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    async def get_deployment(self, deployment_id: UUID) -> PaperDeployment | None:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow("SELECT * FROM paper_deployment WHERE id = $1", deployment_id)
        return _row_to_deployment(row) if row is not None else None

    async def list_deployments(self, tenant_id: UUID) -> list[PaperDeployment]:
        async with tenant_transaction(self._pool, tenant_id) as conn:
            rows = await conn.fetch(
                "SELECT * FROM paper_deployment WHERE tenant_id = $1 ORDER BY created_at",
                tenant_id,
            )
        return [_row_to_deployment(row) for row in rows]

    async def list_running_deployments(self) -> list[PaperDeployment]:
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT * FROM paper_deployment WHERE state = 'RUNNING' ORDER BY created_at"
            )
        return [_row_to_deployment(row) for row in rows]

    async def get_deployment_by_request_key(
        self, tenant_id: UUID, request_idempotency_key: str
    ) -> PaperDeployment | None:
        async with tenant_transaction(self._pool, tenant_id) as conn:
            row = await conn.fetchrow(
                "SELECT * FROM paper_deployment "
                "WHERE tenant_id = $1 AND request_idempotency_key = $2",
                tenant_id,
                request_idempotency_key,
            )
        return _row_to_deployment(row) if row is not None else None

    async def insert_deployment(self, deployment: PaperDeployment) -> PaperDeployment:
        async with tenant_transaction(self._pool, deployment.tenant_id) as conn:
            row = await conn.fetchrow(
                "INSERT INTO paper_deployment "
                "(tenant_id, connection_id, package_ref, mandate_revision_id, adapter_type, "
                " credential_class, endpoint_classification, provider_sandbox_account_ref, "
                " state, fence_token, request_idempotency_key, request_digest) "
                "VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12) "
                "ON CONFLICT (tenant_id, request_idempotency_key) DO NOTHING "
                "RETURNING *",
                deployment.tenant_id,
                deployment.connection_id,
                deployment.package_ref,
                deployment.mandate_revision_id,
                deployment.provenance.adapter_type,
                deployment.provenance.credential_class.value,
                deployment.provenance.endpoint_classification,
                deployment.provenance.provider_sandbox_account_ref,
                deployment.state.value,
                deployment.fence_token,
                deployment.request_idempotency_key,
                deployment.request_digest,
            )
            if row is None:
                # Lost the race — another request with the same
                # (tenant_id, request_idempotency_key) already committed. Return that row
                # as-is (ON CONFLICT DO NOTHING + re-read pattern per CON-006).
                assert deployment.request_idempotency_key is not None
                row = await conn.fetchrow(
                    "SELECT * FROM paper_deployment "
                    "WHERE tenant_id = $1 AND request_idempotency_key = $2",
                    deployment.tenant_id,
                    deployment.request_idempotency_key,
                )
                assert row is not None
        return _row_to_deployment(row)

    async def get_command_by_idempotency_key(
        self, deployment_id: UUID, idempotency_key: str
    ) -> DeploymentCommand | None:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT * FROM deployment_command WHERE deployment_id = $1 "
                "AND idempotency_key = $2",
                deployment_id,
                idempotency_key,
            )
        return _row_to_command(row) if row is not None else None

    async def insert_command(
        self,
        *,
        deployment_id: UUID,
        idempotency_key: str,
        command_type: CommandType,
        actor_subject_id: UUID,
        outcome: CommandOutcome,
        detail: str | None,
    ) -> DeploymentCommand:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                "INSERT INTO deployment_command "
                "(deployment_id, idempotency_key, command_type, actor_subject_id, outcome, "
                " detail) "
                "VALUES ($1, $2, $3, $4, $5, $6) RETURNING *",
                deployment_id,
                idempotency_key,
                command_type.value,
                actor_subject_id,
                outcome.value,
                detail,
            )
        return _row_to_command(row)

    async def transition_deployment_state(
        self,
        deployment_id: UUID,
        *,
        tenant_id: UUID,
        expected_state: str,
        new_state: str,
    ) -> PaperDeployment:
        async with tenant_transaction(self._pool, tenant_id) as conn:
            row = await conditional_update(
                conn,
                table="paper_deployment",
                id_column="id",
                id_value=deployment_id,
                expected_state_column="state",
                expected_state_value=expected_state,
                set_values={"state": new_state, "updated_at": datetime.now(timezone.utc)},
                extra_conditions={"tenant_id": tenant_id},
            )
        return _row_to_deployment(row)

    async def increment_fence(
        self, deployment_id: UUID, *, tenant_id: UUID, expected_state: str, new_state: str
    ) -> PaperDeployment:
        async with tenant_transaction(self._pool, tenant_id) as conn:
            row = await conn.fetchrow(
                "UPDATE paper_deployment "
                "SET state = $4, fence_token = fence_token + 1, updated_at = now() "
                "WHERE id = $1 AND tenant_id = $2 AND state = $3 "
                "RETURNING *",
                deployment_id,
                tenant_id,
                expected_state,
                new_state,
            )
        if row is None:
            raise ConcurrencyConflictError(
                f"paper_deployment.id={deployment_id}: 기대 상태({expected_state})가 아니라 "
                "전이할 수 없습니다."
            )
        return _row_to_deployment(row)

    async def insert_order_intent(self, intent: PaperOrderIntent) -> PaperOrderIntent:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                "INSERT INTO paper_order_intent "
                "(deployment_id, sequence, fence_token_at_submit, state) "
                "VALUES ($1, $2, $3, $4) RETURNING *",
                intent.deployment_id,
                intent.sequence,
                intent.fence_token_at_submit,
                intent.state,
            )
        return _row_to_intent(row)
