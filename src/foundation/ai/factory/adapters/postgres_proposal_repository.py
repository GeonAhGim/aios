"""asyncpg implementation of `strategy_proposal` -- AI-16.

Spec: docs/specs/L4_ai_research_strategy_factory_v1.0.md §2.3 AI-9
(`application/generate_proposal.py`'s "provider -> schema -> compile ->
proposal save"), §5 "proposal submission: idempotent on (token_id,
script_hash)", §9 AI-16 DoD.
Backs the `ProposalRepository` Protocol `application/generate_proposal.py`
(AI-9) already defines -- that module's own docstring names this leaf as
the one shipping the concrete adapter.

`script_hash` is not a `StrategyProposal` field (`contracts/v1.py` never
carries the DSL-12 compile digest past the moment `generate_proposal`
computes it) -- `save()` recomputes it via the same `compile_source(...)
.script_hash` the generation pipeline already ran moments earlier
(deterministic and side-effect-free, so recomputing it here is not a second
decision, just a second read of the same pure function) so this table can
enforce the idempotency key the Protocol's own `find_by_idempotency_key`
signature requires without adding a field to the wire contract.

`get_for_tenant` is this adapter's own addition beyond the `ProposalRepository`
Protocol -- `src/api/mcp/tools_paper.py` needs the authoritative (not
client-supplied) proposal for a `promote_to_paper` call, scoped to the
calling `AgentToken.tenant_id`. The join through `agent_token` is the only
way to scope a `strategy_proposal` row by tenant at all: the row itself
carries no `tenant_id` column (mirroring `StrategyProposal`'s own field
list, which has none either -- `created_by_token` is the sole ownership
key). A `strategy_proposal` row whose owning token belongs to a different
tenant is indistinguishable from a nonexistent one (`None`), the same
existence-leak discipline `get_experiment`/`get_token` already apply."""

from __future__ import annotations

import json
from uuid import UUID

import asyncpg

from src.core.indicators.registry import DEFAULT_REGISTRY
from src.core.script.artifact.compile import compile_source
from src.foundation.ai.factory.contracts.v1 import DataScope, ProviderRef, StrategyProposal

__all__ = ["PostgresProposalRepository"]


def _row_to_proposal(row: asyncpg.Record) -> StrategyProposal:
    return StrategyProposal(
        proposal_id=row["proposal_id"],
        script_source=row["script_source"],
        hypothesis=row["hypothesis"],
        data_scope=DataScope.model_validate(json.loads(row["data_scope"])),
        params=json.loads(row["params"]),
        provider_ref=ProviderRef(row["provider_ref"]),
        prompt_hash=row["prompt_hash"],
        created_by_token=row["created_by_token"],
    )


def _compute_script_hash(script_source: str) -> str:
    return compile_source(
        script_source, registry_version=DEFAULT_REGISTRY.registry_hash()
    ).script_hash


class PostgresProposalRepository:
    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    async def find_by_idempotency_key(
        self, *, created_by_token: UUID, script_hash: str
    ) -> StrategyProposal | None:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT * FROM strategy_proposal WHERE created_by_token = $1 AND script_hash = $2",
                created_by_token,
                script_hash,
            )
        return None if row is None else _row_to_proposal(row)

    async def save(self, proposal: StrategyProposal) -> None:
        script_hash = _compute_script_hash(proposal.script_source)
        async with self._pool.acquire() as conn:
            await conn.execute(
                "INSERT INTO strategy_proposal "
                "(proposal_id, script_source, hypothesis, data_scope, params, "
                " provider_ref, prompt_hash, script_hash, created_by_token) "
                "VALUES ($1, $2, $3, $4::jsonb, $5::jsonb, $6, $7, $8, $9)",
                proposal.proposal_id,
                proposal.script_source,
                proposal.hypothesis,
                json.dumps(proposal.data_scope.model_dump(mode="json")),
                json.dumps(proposal.params),
                proposal.provider_ref.value,
                proposal.prompt_hash,
                script_hash,
                proposal.created_by_token,
            )

    async def get_for_tenant(
        self, proposal_id: UUID, *, tenant_id: UUID
    ) -> StrategyProposal | None:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT sp.* FROM strategy_proposal sp "
                "JOIN agent_token at ON at.token_id = sp.created_by_token "
                "WHERE sp.proposal_id = $1 AND at.tenant_id = $2",
                proposal_id,
                tenant_id,
            )
        return None if row is None else _row_to_proposal(row)

    async def list_for_tenant(
        self, tenant_id: UUID, *, limit: int = 50
    ) -> tuple[StrategyProposal, ...]:
        """AI-17 -- `api/routers/ai.py`'s proposal list view. Same tenant scoping
        join as `get_for_tenant` (no `tenant_id` column on `strategy_proposal`
        itself), newest first. This adapter's own addition beyond the
        `ProposalRepository` Protocol, same precedent as `get_for_tenant`'s own
        docstring already sets."""
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT sp.* FROM strategy_proposal sp "
                "JOIN agent_token at ON at.token_id = sp.created_by_token "
                "WHERE at.tenant_id = $1 ORDER BY sp.created_at DESC LIMIT $2",
                tenant_id,
                limit,
            )
        return tuple(_row_to_proposal(row) for row in rows)
