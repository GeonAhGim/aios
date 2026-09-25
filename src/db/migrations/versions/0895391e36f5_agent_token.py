"""AI-4 -- agent_token(opaque scoped credential storage).

Revision ID: 0895391e36f5
Revises: 052b26dfb97b

Spec: docs/specs/L4_ai_research_strategy_factory_v1.0.md §2.1 AI-4, §3
("token: opaque 32 bytes, the server stores only sha256"), §9 AI-4 DoD
("opaque storage, cross-tenant 404").

`token_hash` is a sha256 hex digest (64 chars) -- the plaintext opaque
secret is exposed only in the issuance response and is never stored in the
DB (same pattern as auth_session.refresh_hash, a9445f6ca04c). `paper_only`
is pinned to TRUE via CHECK -- spec §1's invariant ("AI never inherits a
human session's authority") is enforced twice, once by the domain layer
(`token_rules.AgentToken.__post_init__`) and again here (defense against a
direct INSERT that bypasses the domain layer). `scopes` being non-empty is
likewise CHECK-enforced -- the same line as the domain layer's "at least one
scope" invariant.
"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0895391e36f5"
down_revision: str | Sequence[str] | None = "052b26dfb97b"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE agent_token (
            token_id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id         UUID NOT NULL,
            token_hash        CHAR(64) NOT NULL UNIQUE,
            scopes            TEXT[] NOT NULL CHECK (array_length(scopes, 1) > 0),
            allow_instruments TEXT[] NOT NULL DEFAULT '{}',
            notional_cap      NUMERIC NOT NULL,
            paper_only        BOOLEAN NOT NULL DEFAULT TRUE CHECK (paper_only IS TRUE),
            issued_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
            expires_at        TIMESTAMPTZ NOT NULL,
            revoked_at        TIMESTAMPTZ,
            revoke_reason     VARCHAR(50)
        )
        """
    )
    op.execute(
        "CREATE INDEX idx_agent_token_tenant_active "
        "ON agent_token(tenant_id) WHERE revoked_at IS NULL"
    )


def downgrade() -> None:
    op.execute("DROP TABLE agent_token")
