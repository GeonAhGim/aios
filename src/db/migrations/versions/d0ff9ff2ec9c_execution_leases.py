"""execution_leases — EO-02

Revision ID: d0ff9ff2ec9c
Revises: b3c7f19ad2e6
Create Date: 2026-09-04 00:00:00.000000

Spec: docs/specs/L4_execution_ownership_and_safety_gate_wiring_v1.0.md
§2-A, §3.1. `ExecutionLoopScheduler`가 동시에 2개 이상 뜨는 배포 상황에서
같은 execution을 중복 tick하지 못하도록(§1 P0-R1) 리스를 저장하는 테이블.
`execution_id`가 PK이자 `strategy_executions(id)` FK — 한 execution에
리스는 항상 최대 1행(현재 소유자)만 존재한다. `ON DELETE CASCADE`는
`strategy_executions` 행이 지워지면 리스도 함께 정리되도록 한다(고아 리스
방지). `ix_execution_leases_expires_at`는 만료된 리스를 찾는 정리/관측
쿼리를 위한 색인(§6, §7)."""
from collections.abc import Sequence

from alembic import op

revision: str = "d0ff9ff2ec9c"
down_revision: str | Sequence[str] | None = "b3c7f19ad2e6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE execution_leases (
            execution_id BIGINT PRIMARY KEY
                REFERENCES strategy_executions(id) ON DELETE CASCADE,
            owner_id TEXT NOT NULL,
            fencing_token BIGINT NOT NULL DEFAULT 0,
            heartbeat_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            expires_at TIMESTAMPTZ NOT NULL
        )
        """
    )
    op.execute(
        "CREATE INDEX ix_execution_leases_expires_at ON execution_leases (expires_at)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_execution_leases_expires_at")
    op.execute("DROP TABLE IF EXISTS execution_leases")
