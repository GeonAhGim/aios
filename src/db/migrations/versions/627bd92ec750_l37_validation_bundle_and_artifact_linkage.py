"""L37 -- validation bundle table + artifact-linkage columns (migration M3)

Revision ID: 627bd92ec750
Revises: 2e97e28fe878
Create Date: 2026-09-22 00:00:00.000000

Spec: docs/specs/L4_strategy_portfolio_backtest_v1.0.md §9 L37.

Expand-only (ADR-2026-09-10-C §5): every new column on the two existing
tables is nullable or carries a static default, so a not-yet-upgraded app
instance's existing INSERT/SELECT statements keep working during a rolling
deploy -- no contract (NOT NULL enforcement / column drop) step is bundled
here.

`strategy_validation_bundle` mirrors `domain/models.py`'s `ValidationBundle`
(added ahead of this leaf, see that module's L37 docstring) -- the UNIQUE
constraint is the DB-level backstop for "persisted once per (artifact_hash,
policy_version, data_snapshot_hash)", and the CHECK constraint is the
DB-level backstop for I6's forward direction ("hard_fail_reasons non-empty
implies outcome=FAIL") -- the reverse direction (FAIL implies non-empty
reasons) is enforced only in `ValidationBundle.__post_init__`, same split the
domain module's docstring already documents.
"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "627bd92ec750"
down_revision: str | None = "2e97e28fe878"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE strategy_validation_run
            ADD COLUMN artifact_hash VARCHAR(64),
            ADD COLUMN policy_version VARCHAR(20) NOT NULL DEFAULT 'vp-v1',
            ADD COLUMN seed INT NOT NULL DEFAULT 0,
            ADD COLUMN data_snapshot_hash VARCHAR(64),
            ADD COLUMN trace_id VARCHAR(64)
        """
    )
    op.execute(
        """
        ALTER TABLE strategy_validation_result
            ADD COLUMN evidence_refs TEXT[] NOT NULL DEFAULT '{}'
        """
    )
    op.execute(
        """
        CREATE TABLE strategy_validation_bundle (
            id                 UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            artifact_hash      VARCHAR(64) NOT NULL,
            policy_version     VARCHAR(20) NOT NULL,
            data_snapshot_hash VARCHAR(64) NOT NULL,
            outcome            VARCHAR(30) NOT NULL
                CHECK (outcome IN ('PASS', 'FAIL', 'PASS_WITH_OBLIGATIONS')),
            check_run_ids      UUID[] NOT NULL,
            bundle_hash        VARCHAR(64) NOT NULL,
            hard_fail_reasons  TEXT[] NOT NULL DEFAULT '{}',
            obligations        TEXT[] NOT NULL DEFAULT '{}',
            created_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
            -- 76번 §1 재현성/멱등성 -- 같은 정확한 (artifact, policy, snapshot)
            -- 조합은 재평가가 아니라 기존 번들을 그대로 반환한다.
            UNIQUE (artifact_hash, policy_version, data_snapshot_hash),
            -- I6 forward direction (domain/models.py's ValidationBundle
            -- enforces both directions at construction; this is the DB-level
            -- backstop for a row inserted by a future path that bypasses it).
            CHECK (
                cardinality(hard_fail_reasons) = 0 OR outcome = 'FAIL'
            )
        )
        """
    )
    # Same append-only-audit principle as strategy_validation_result above.
    op.execute("REVOKE UPDATE, DELETE ON strategy_validation_bundle FROM PUBLIC")


def downgrade() -> None:
    op.execute("DROP TABLE strategy_validation_bundle")
    op.execute(
        """
        ALTER TABLE strategy_validation_result
            DROP COLUMN evidence_refs
        """
    )
    op.execute(
        """
        ALTER TABLE strategy_validation_run
            DROP COLUMN artifact_hash,
            DROP COLUMN policy_version,
            DROP COLUMN seed,
            DROP COLUMN data_snapshot_hash,
            DROP COLUMN trace_id
        """
    )
