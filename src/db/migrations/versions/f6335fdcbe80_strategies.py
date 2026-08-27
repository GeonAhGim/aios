"""strategies

Revision ID: f6335fdcbe80
Revises: 1fd699c0c44c
Create Date: 2026-08-28 00:26:09.061936

Spec: 04_db_schema_v1.6.md (Strategies, 9.11 FSMStrategyConfig)

편차: owner_user_id의 REFERENCES users(user_id)는 11.1에서 ALTER TABLE로
추가한다(tasks.user_id와 동일 이유 — a7c02fa80d22 참조).
"""
from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "f6335fdcbe80"
down_revision: str | Sequence[str] | None = "1fd699c0c44c"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE strategies (
            strategy_id     VARCHAR(100) NOT NULL,
            version         VARCHAR(20) NOT NULL,
            owner_user_id   UUID NOT NULL,  -- FK: 11.1에서 ALTER TABLE로 추가
            target_asset    VARCHAR(50) NOT NULL,
            market          VARCHAR(30) NOT NULL,
            exchange        VARCHAR(30) NOT NULL,
            fsm_definition  JSONB NOT NULL,
            author_agent    VARCHAR(100) NOT NULL,
            lifecycle_status VARCHAR(30) NOT NULL DEFAULT 'IDEA'
                CHECK (lifecycle_status IN (
                    'IDEA','RESEARCH','GENERATED','BACKTESTING','VALIDATING','STRESS_TESTING',
                    'RISK_REVIEW','PAPER_TRADING','APPROVED','DEPLOYED','MONITORING','REVIEW',
                    'RETIRED','REJECTED','FAILED'
                )),
            certified_badge BOOLEAN NOT NULL DEFAULT FALSE,
            last_recertified_at TIMESTAMPTZ,
            created_via     VARCHAR(20) NOT NULL DEFAULT 'EDITOR'
                CHECK (created_via IN ('EDITOR','AI_GENERATED','EVOLUTIONARY')),
            created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),

            PRIMARY KEY (strategy_id, version)
        )
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE strategies")
