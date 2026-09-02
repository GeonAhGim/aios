"""foundation_performance — FND-09 Performance Reporting (M5)

Revision ID: 6e5baa1c7a55
Revises: d2c8b1e4f6a0
Create Date: 2026-09-02 16:20:00.000000

Spec: docs/specs/L4_strategy_portfolio_backtest_v1.0.md §2.6/§3.7 M5.

`performance_statement`는 `REVOKE UPDATE, DELETE`(WORM, foundation_audit_event/
audit_log와 같은 원칙) — 정정은 `state=CORRECTED`인 새 리비전을 append하는
것으로 표현한다(correct_statement.py, L49). `UNIQUE(tenant_id, scope,
scope_ref, period_start, period_end, methodology_version, revision_no)`가
같은 기간·방법론에 같은 리비전 번호가 두 번 만들어지는 걸 DB 레벨에서
막는다.
"""
from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "6e5baa1c7a55"
down_revision: str | None = "d2c8b1e4f6a0"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE performance_methodology (
            version           VARCHAR(20) PRIMARY KEY,
            methodology_hash  VARCHAR(64) NOT NULL,
            definition        JSONB NOT NULL,
            created_at        TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )

    op.execute(
        """
        CREATE TABLE valuation_snapshot (
            id                     UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id              UUID NOT NULL REFERENCES users(user_id),
            scope                  VARCHAR(10) NOT NULL CHECK (scope IN ('PAPER', 'LIVE')),
            scope_ref              VARCHAR(200) NOT NULL,
            as_of                  TIMESTAMPTZ NOT NULL,
            positions              JSONB NOT NULL,
            cash                   JSONB NOT NULL,
            price_evidence         JSONB NOT NULL,
            reconciliation_run_id  UUID,
            state                  VARCHAR(20) NOT NULL
                CHECK (state IN ('ESTIMATED', 'RECONCILED')),
            created_at             TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    op.execute(
        "CREATE INDEX ix_valuation_snapshot_scope_ref "
        "ON valuation_snapshot (tenant_id, scope, scope_ref, as_of DESC)"
    )

    op.execute(
        """
        CREATE TABLE performance_statement (
            id                    UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id             UUID NOT NULL REFERENCES users(user_id),
            scope                 VARCHAR(10) NOT NULL CHECK (scope IN ('PAPER', 'LIVE')),
            scope_ref             VARCHAR(200) NOT NULL,
            period_start          TIMESTAMPTZ NOT NULL,
            period_end            TIMESTAMPTZ NOT NULL,
            as_of                 TIMESTAMPTZ NOT NULL,
            methodology_version   VARCHAR(20) NOT NULL REFERENCES performance_methodology(version),
            input_refs            JSONB NOT NULL,
            components            JSONB NOT NULL,
            returns               JSONB NOT NULL,
            risk                  JSONB NOT NULL,
            benchmark             JSONB,
            benchmark_ref         TEXT,
            state                 VARCHAR(20) NOT NULL
                CHECK (state IN ('ESTIMATED', 'FINAL', 'CORRECTED')),
            revision_no           INT NOT NULL CHECK (revision_no > 0),
            prior_statement_id    UUID REFERENCES performance_statement(id),
            identity_ok           BOOLEAN NOT NULL,
            identity_residual     NUMERIC(20, 8),
            limitations           TEXT[] NOT NULL DEFAULT '{}',
            evidence_refs         JSONB NOT NULL DEFAULT '[]',
            created_at            TIMESTAMPTZ NOT NULL DEFAULT now(),
            UNIQUE (tenant_id, scope, scope_ref, period_start, period_end,
                    methodology_version, revision_no)
        )
        """
    )
    op.execute(
        "CREATE INDEX ix_performance_statement_scope "
        "ON performance_statement (tenant_id, scope, period_end DESC)"
    )
    op.execute("REVOKE UPDATE, DELETE ON performance_statement FROM PUBLIC")

    op.execute(
        """
        CREATE TABLE performance_attribution_slice (
            id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            statement_id  UUID NOT NULL REFERENCES performance_statement(id),
            dimension     VARCHAR(50) NOT NULL,
            key           VARCHAR(100) NOT NULL,
            contribution  NUMERIC(20, 8) NOT NULL,
            confidence    NUMERIC(5, 4),
            limitation    TEXT
        )
        """
    )
    op.execute(
        "CREATE INDEX ix_performance_attribution_slice_statement_id "
        "ON performance_attribution_slice (statement_id)"
    )


def downgrade() -> None:
    op.execute("DROP TABLE performance_attribution_slice")
    op.execute("DROP TABLE performance_statement")
    op.execute("DROP TABLE valuation_snapshot")
    op.execute("DROP TABLE performance_methodology")
