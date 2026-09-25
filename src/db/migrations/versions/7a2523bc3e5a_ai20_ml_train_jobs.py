"""AI-20 -- ml_train_jobs: resumable checkpointed training jobs.

Revision ID: 7a2523bc3e5a
Revises: f85e5d761d6b

Spec: docs/specs/L4_ai_research_strategy_factory_v1.0.md §2.5 AI-20
(`application/train_job.py` + `ports/train_job_repository.py` +
`adapters/postgres_train_job_repository.py`), §5 ("training job:
checkpoint conditional UPDATE, resumable"), §9 AI-20 DoD ("resumable").

`checkpoint` holds `adapters/local_trainer.py::LocalTrainer`'s opaque
`Booster.model_to_string()` text -- plain `TEXT`, not `JSONB`, since no SQL
ever needs to query inside it (same "opaque blob" treatment AI-19's
`ml_model_registry.drift_baseline` gives per-feature sample arrays, except
those *are* JSONB because `PostgresModelRegistry` round-trips them through
`json.loads`/`json.dumps`; this column round-trips through
`PostgresTrainJobRepository` as a bare string).

`rounds_completed` is the compare-and-swap column
`src.core.db.conditional_write.conditional_update` keys off of
(`ports/train_job_repository.py::TrainJobRepositoryPort.checkpoint`'s own
docstring) -- the CHECK constraint below is defense in depth against a
hand-built UPDATE bypassing the port, same reasoning as AI-19's
`f85e5d761d6b` lineage-span CHECKs.

`status` only ever moves `running -> completed` (`application/train_job.py`
never marks a job `failed`; an exception mid-`train_step` simply leaves the
row at its last successfully checkpointed `rounds_completed`/`checkpoint`,
which is what makes the next `run_train_job(job_id=...)` call resumable
instead of needing a distinct "retry a failed job" code path) -- so the
CHECK only needs to allow those two values, and `aios_app` gets UPDATE
(unlike AI-19's insert-once `ml_model_registry`) because checkpointing is
this table's whole purpose.
"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "7a2523bc3e5a"
down_revision: str | Sequence[str] | None = "f85e5d761d6b"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_APP_ROLE = "aios_app"
_TABLE = "ml_train_jobs"


def upgrade() -> None:
    op.execute(
        f"""
        CREATE TABLE {_TABLE} (
            job_id              TEXT PRIMARY KEY,
            model_id            TEXT NOT NULL,
            status              TEXT NOT NULL DEFAULT 'running',
            rounds_completed    INTEGER NOT NULL DEFAULT 0,
            checkpoint          TEXT,
            metrics             JSONB NOT NULL DEFAULT '{{}}'::jsonb,
            created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
            CHECK (status IN ('running', 'completed')),
            CHECK (rounds_completed >= 0)
        )
        """
    )
    op.execute(f"CREATE INDEX ix_{_TABLE}_model_id ON {_TABLE} (model_id)")
    op.execute(f"GRANT SELECT, INSERT, UPDATE ON {_TABLE} TO {_APP_ROLE}")


def downgrade() -> None:
    op.execute(f"DROP TABLE {_TABLE}")
