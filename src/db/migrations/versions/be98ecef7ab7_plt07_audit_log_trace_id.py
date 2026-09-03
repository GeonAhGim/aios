"""PLT-07 — audit_log.trace_id 컬럼 추가.

Revision ID: be98ecef7ab7
Revises: 4a1d0c0de009
Create Date: 2026-09-03 19:36:07.468294

Spec: docs/specs/L4_platform_observability_tenancy_api_v1.0.md §2.1(A) PLT-07.

trace_id는 PLT-01 `src/core/observability/context.py`의 `RequestContext.trace_id`
값을 그대로 옮겨 적는 컬럼이다 — 새 진실 소스를 만들지 않는다(task-906 decision).
NULL을 허용한다: 기존 행은 trace_id가 없던 시절에 쓰였고, backfill하지 않는다
(과거 요청의 실제 trace_id는 복원 불가능하므로 임의값을 채우면 오히려 상관관계를
조작하는 셈이다).

`audit_log`는 `4a1d0c0de001`에서 이미 `BEFORE UPDATE OR DELETE` WORM 트리거가
소급 적용됐다 — `ALTER TABLE ... ADD COLUMN`은 트리거·REVOKE에 영향을 주지
않는다(트리거는 컬럼이 아니라 UPDATE/DELETE 연산 자체에 걸림).
"""
from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "be98ecef7ab7"
down_revision: str | Sequence[str] | None = "4a1d0c0de009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("ALTER TABLE audit_log ADD COLUMN trace_id UUID")
    op.execute("CREATE INDEX idx_audit_log_trace_id ON audit_log(trace_id)")


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_audit_log_trace_id")
    op.execute("ALTER TABLE audit_log DROP COLUMN trace_id")
