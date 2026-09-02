"""data_distrust_state — 9.5(R-48) Data Distrust Mode 영속 상태

Revision ID: 9744695fa220
Revises: 6e5baa1c7a55
Create Date: 2026-09-03 01:25:00

DataDistrustMonitor(src/core/safety/data_distrust.py)는 인메모리 상태라
재시작마다 모든 심볼이 NORMAL로 리셋됐다 — DISTRUSTED에서 벗어나려면
편차가 exit_sustain_seconds 동안 유지돼야 한다는 히스테리시스 원칙이
재시작 한 번에 무력화되는 결함(전수감사 §R9). (exchange, symbol) 단일
행에 최신 판정만 유지하는 상태 테이블 — 이력이 아니라 스냅샷.
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "9744695fa220"
down_revision: str | None = "6e5baa1c7a55"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "data_distrust_state",
        sa.Column("exchange", sa.String(30), nullable=False),
        sa.Column("symbol", sa.String(30), nullable=False),
        sa.Column(
            "level",
            sa.String(30),
            nullable=False,
            server_default="NORMAL",
        ),
        sa.Column(
            "since", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("sources_available", sa.SmallInteger(), nullable=False, server_default="1"),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.PrimaryKeyConstraint("exchange", "symbol"),
        sa.CheckConstraint(
            "level IN ('NORMAL','SUSPICIOUS','DISTRUSTED','DEGRADED_SINGLE_SOURCE')",
            name="data_distrust_state_level_check",
        ),
    )


def downgrade() -> None:
    op.drop_table("data_distrust_state")
