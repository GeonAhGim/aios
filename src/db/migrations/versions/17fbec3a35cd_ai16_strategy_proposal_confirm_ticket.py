"""AI-16 -- strategy_proposal(AI-9 storage) + confirm_ticket(AI-2/13 storage).

Revision ID: 17fbec3a35cd
Revises: c3f8a1d29b6e

Spec: docs/specs/L4_ai_research_strategy_factory_v1.0.md §2.1 AI-4/§2.3 AI-9
("proposal save"), §9 AI-16 DoD ("confirmation token round trip, adversarial
reuse -> 409"). Two tables neither AI-9 (`generate_proposal.py`) nor AI-13
(`promote_to_paper.py`) shipped an adapter for -- both leaves' own
docstrings name this leaf (AI-16) as the one that adds the concrete
Postgres storage behind their `ProposalRepository`/`ConfirmTicketRepository`
Protocols.

`strategy_proposal.script_hash` is not one of `StrategyProposal`'s own
fields (`contracts/v1.py`) -- it is `PostgresProposalRepository`'s own
idempotency-index column, recomputed at save time from `script_source` via
the same DSL-12 `compile_source(...).script_hash` the generation pipeline
already used, and looked up by `find_by_idempotency_key(created_by_token,
script_hash)` (spec §5 "proposal submission: idempotent on (token_id,
script_hash)"). The
`UNIQUE(created_by_token, script_hash)` constraint is that idempotency
guarantee's second line of defense (a concurrent duplicate INSERT is
rejected at the DB layer even if the application-level lookup racily missed
it), the same "conditional UPDATE / unique constraint as the real
concurrency guard" posture standard-105 already applies elsewhere.

`confirm_ticket` is AI-2's `domain/confirm.py::ConfirmTicket` shape
verbatim (`ticket_id, action_digest, expires_at, consumed_at`) plus
`tenant_id`/`created_by_token`/`issued_at` persistence metadata (the same
addition `agent_token`, migration 0895391e36f5, already made for its own
domain type). The single-use guarantee is the standard-105 conditional
UPDATE `PostgresConfirmTicketRepository.mark_consumed` performs (`WHERE
consumed_at IS NULL`) -- this migration does not need a separate UNIQUE
constraint for that (unlike the idempotency case above, there is exactly
one ticket per promotion attempt, never a concurrent duplicate insert to
guard against).
"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "17fbec3a35cd"
down_revision: str | Sequence[str] | None = "c3f8a1d29b6e"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE strategy_proposal (
            proposal_id       UUID PRIMARY KEY,
            script_source     TEXT NOT NULL,
            hypothesis        VARCHAR(2000) NOT NULL,
            data_scope        JSONB NOT NULL,
            params            JSONB NOT NULL DEFAULT '{}'::jsonb,
            provider_ref      VARCHAR(20) NOT NULL,
            prompt_hash       CHAR(64) NOT NULL,
            script_hash       CHAR(64) NOT NULL,
            created_by_token  UUID NOT NULL REFERENCES agent_token(token_id),
            created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
            UNIQUE (created_by_token, script_hash)
        )
        """
    )
    op.execute(
        "CREATE INDEX idx_strategy_proposal_created_by_token ON strategy_proposal(created_by_token)"
    )
    op.execute(
        """
        CREATE TABLE confirm_ticket (
            ticket_id         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id         UUID NOT NULL,
            created_by_token  UUID NOT NULL REFERENCES agent_token(token_id),
            action_digest     CHAR(64) NOT NULL,
            issued_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
            expires_at        TIMESTAMPTZ NOT NULL,
            consumed_at       TIMESTAMPTZ
        )
        """
    )
    op.execute(
        "CREATE INDEX idx_confirm_ticket_tenant_active "
        "ON confirm_ticket(tenant_id) WHERE consumed_at IS NULL"
    )


def downgrade() -> None:
    op.execute("DROP TABLE confirm_ticket")
    op.execute("DROP TABLE strategy_proposal")
