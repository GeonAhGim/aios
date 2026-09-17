"""AI-19 -- ml_model_registry: model lineage storage.

Revision ID: f85e5d761d6b
Revises: 17fbec3a35cd

Spec: docs/specs/L4_ai_research_strategy_factory_v1.0.md §2.5 AI-19
(`postgres_model_registry` + migration), §9 AI-19 DoD ("lineage storage").

`train_lineage_start/end/source_ref` flatten AI-18's `TrainDataLineage`
(`ml/contracts/v1.py`) into the row -- there is no separate lineage table,
because AI-19's DoD is "each registered model durably stores its own
training lineage", not "lineage is queryable across models" (a cross-model
view, if ever needed, is an AI-20/21 concern once training jobs exist).

Two CHECK constraints mirror invariants the Pydantic contract already
enforces (`TrainDataLineage._check_span`, `ModelCard._check_trained_at_
after_lineage`) -- defense in depth against a caller bypassing the
contract with a hand-built INSERT, same reasoning as AI-10's `kind` CHECK
(`c3f8a1d29b6e`).

`registered_at` is this adapter's own ordering column, not one of
`ModelCard`'s own fields -- `PostgresModelRegistry.latest()` orders by it
as a tiebreaker when two versions share the same `trained_at` (same
precedent as AI-16's `strategy_proposal.script_hash`,
`17fbec3a35cd`).

No UPDATE/DELETE is granted to `aios_app` -- a model registration is
insert-once; `PostgresModelRegistry.register()`'s `ON CONFLICT ... DO
NOTHING` handles idempotent retries in application code, so the table does
not need WORM's REVOKE+trigger machinery the way AI-10's `experiments`
does (that leaf's DoD is specifically "append-only proof"; this leaf's is
"lineage storage").
"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "f85e5d761d6b"
down_revision: str | Sequence[str] | None = "17fbec3a35cd"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_APP_ROLE = "aios_app"
_TABLE = "ml_model_registry"


def upgrade() -> None:
    op.execute(
        f"""
        CREATE TABLE {_TABLE} (
            model_id                    TEXT NOT NULL,
            version                     TEXT NOT NULL,
            model_hash                  CHAR(64) NOT NULL,
            train_lineage_start         TIMESTAMPTZ NOT NULL,
            train_lineage_end           TIMESTAMPTZ NOT NULL,
            train_lineage_source_ref    TEXT NOT NULL,
            trained_at                  TIMESTAMPTZ NOT NULL,
            metrics                     JSONB NOT NULL,
            drift_baseline              JSONB NOT NULL,
            registered_at               TIMESTAMPTZ NOT NULL DEFAULT now(),
            PRIMARY KEY (model_id, version),
            CHECK (train_lineage_end >= train_lineage_start),
            CHECK (trained_at >= train_lineage_end)
        )
        """
    )
    op.execute(
        f"CREATE INDEX ix_{_TABLE}_model_trained "
        f"ON {_TABLE} (model_id, trained_at DESC, registered_at DESC)"
    )
    op.execute(f"GRANT SELECT, INSERT ON {_TABLE} TO {_APP_ROLE}")


def downgrade() -> None:
    op.execute(f"DROP TABLE {_TABLE}")
