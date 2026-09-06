"""FA-2c — foundation 8 테이블에 FORCE ROW LEVEL SECURITY.

Revision ID: c9f4e2a1b6d7
Revises: a0e7e1454b60
Create Date: 2026-09-06 00:00:00.000000

Spec: docs/specs/L4_platform_observability_tenancy_api_v1.0.md §2 M5, §9
PLT-30. task-1718 P0-E — b3c7f19ad2e6이 만든 정책은 살아있지만 `ENABLE ROW
LEVEL SECURITY`(FORCE 아님)라 그 8개 테이블의 소유자 롤(`aios_migrator`, 각
CREATE TABLE 실행 계정)은 여전히 RLS를 우회한다. `ALTER TABLE ... FORCE ROW
LEVEL SECURITY`는 정책·컬럼·데이터를 전혀 바꾸지 않고 "테이블 소유자에게도
정책을 적용한다"는 플래그 하나만 켠다 — 슈퍼유저는 이 플래그와 무관하게
언제나 우회한다(PostgreSQL 문서, `FORCE`로도 막을 수 없음). 그래서
`tests/integration/core/db/test_rls_foundation.py` 등 이 프로젝트의 RLS
테스트는 슈퍼유저 세션 안에서 `SET ROLE aios_app`으로 실제 권한을 낮춰
검증한다(AppRoleTx) — 이 리프가 새로 추가하는 `SET ROLE aios_migrator`
검증도 같은 방식이다.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "c9f4e2a1b6d7"
down_revision: str | Sequence[str] | None = "a0e7e1454b60"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_FOUNDATION_TABLES = (
    "consent_record",
    "account_connection",
    "risk_evaluation",
    "paper_deployment",
    "reconciliation_run",
    "valuation_snapshot",
    "portfolio_mandate",
    "foundation_audit_event",
)


def upgrade() -> None:
    for table in _FOUNDATION_TABLES:
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")


def downgrade() -> None:
    for table in reversed(_FOUNDATION_TABLES):
        op.execute(f"ALTER TABLE {table} NO FORCE ROW LEVEL SECURITY")
