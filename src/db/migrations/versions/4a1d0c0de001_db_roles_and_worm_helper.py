"""L0-5 — DB 역할 분리(aios_migrator/aios_app) + WORM 트리거 소급 적용.

Revision ID: 4a1d0c0de001
Revises: 9744695fa220
Create Date: 2026-09-03 00:00:00.000000

Spec: docs/specs/L4_market_data_positions_ledger_v1.0.md §9 L0-5.

L0-3([[src/core/db/roles.py]]·[[src/core/db/append_only.py]])이 SQL 생성기만
만들고 실행은 이 리프로 미뤘다 — 여기서 실제로 `aios_migrator`(테이블
소유자)·`aios_app`(런타임 애플리케이션 DML 전용) 역할을 만들고, 기존
`REVOKE ... FROM PUBLIC`만으로는 테이블 소유자에게 WORM이 강제되지 않는다는
문제(9ec8a1ee28d7·4453afe74725 docstring이 남긴 미해결 항목)를 `BEFORE
UPDATE OR DELETE` 트리거로 소급 해결한다.

소급 대상은 task-165 decision에 따라 3개로 한정한다: `audit_log`,
`foundation_audit_event`, `wallet_transactions`. 다른 WORM 대상 테이블
(`pos_journal` 등)은 아직 존재하지 않는다(LB-8 등 후속 리프) — `worm_sql()`은
테이블을 새로 만드는 마이그레이션 자체에서 호출하는 것이 기본 패턴이고,
이 3개만 예외적으로 이미 존재하는 상태에 소급 적용한다.

편차(downgrade): `audit_log`·`foundation_audit_event`는 이 마이그레이션
이전부터 이미 `REVOKE UPDATE, DELETE ... FROM PUBLIC`이 걸려 있었다
(9ec8a1ee28d7, 4453afe74725). `worm_drop_sql()`의 마지막 문장(PUBLIC에
UPDATE/DELETE 재부여)을 그대로 실행하면 이 리프 적용 이전 상태가 아니라
"WORM을 아예 도입하기 전" 상태로 과도하게 되돌아간다 — 그래서 이 두
테이블은 트리거·가드 함수만 제거하고 REVOKE는 유지한다. `wallet_transactions`는
이 리프 이전에 REVOKE가 없었으므로 `worm_drop_sql()` 전체(REVOKE 해제 포함)를
그대로 쓴다.

역할(`aios_migrator`/`aios_app`) 자체는 downgrade에서 DROP하지 않는다 —
클러스터 전역 객체라 다른 세션이 이미 그 역할로 접속해 있을 수 있고, 소유
객체가 있으면 `DROP ROLE`이 실패한다. 대신 이 마이그레이션이 부여한 스키마
권한만 REVOKE한다 — round-trip(upgrade → downgrade → upgrade)에는 영향 없다
(역할이 이미 있으면 `CREATE ROLE ... IF NOT EXISTS` 대체 DO 블록이 스킵된다).
"""
from collections.abc import Sequence

from alembic import op

from src.core.db.append_only import worm_drop_sql, worm_sql
from src.core.db.roles import ensure_roles_sql

# revision identifiers, used by Alembic.
revision: str = "4a1d0c0de001"
down_revision: str | Sequence[str] | None = "9744695fa220"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_APP_ROLE = "aios_app"
_MIGRATOR_ROLE = "aios_migrator"

# 이 리프 이전부터 REVOKE UPDATE, DELETE FROM PUBLIC이 걸려 있던 테이블 —
# downgrade에서 worm_drop_sql()의 마지막 GRANT 문은 건너뛴다(위 모듈 docstring).
_WORM_RETROFIT_ALREADY_REVOKED = ("audit_log", "foundation_audit_event")
# 이 리프 이전에는 REVOKE가 전혀 없던 테이블 — downgrade에서 완전히 원복한다.
_WORM_RETROFIT_NEWLY_REVOKED = ("wallet_transactions",)


def upgrade() -> None:
    for statement in ensure_roles_sql(app_role=_APP_ROLE, migrator_role=_MIGRATOR_ROLE):
        op.execute(statement)
    for table in _WORM_RETROFIT_ALREADY_REVOKED + _WORM_RETROFIT_NEWLY_REVOKED:
        for statement in worm_sql(table):
            op.execute(statement)


def downgrade() -> None:
    for table in reversed(_WORM_RETROFIT_NEWLY_REVOKED):
        for statement in worm_drop_sql(table):
            op.execute(statement)
    for table in reversed(_WORM_RETROFIT_ALREADY_REVOKED):
        for statement in worm_drop_sql(table)[:-1]:  # 마지막 GRANT는 건너뛴다
            op.execute(statement)

    op.execute(f"REVOKE ALL PRIVILEGES ON SCHEMA public FROM {_MIGRATOR_ROLE}")
    op.execute(f"REVOKE USAGE ON SCHEMA public FROM {_APP_ROLE}")
    op.execute(
        f"REVOKE SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public FROM {_APP_ROLE}"
    )
    op.execute(f"REVOKE USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public FROM {_APP_ROLE}")
    op.execute(
        f"ALTER DEFAULT PRIVILEGES FOR ROLE {_MIGRATOR_ROLE} IN SCHEMA public "
        f"REVOKE SELECT, INSERT, UPDATE, DELETE ON TABLES FROM {_APP_ROLE}"
    )
    op.execute(
        f"ALTER DEFAULT PRIVILEGES FOR ROLE {_MIGRATOR_ROLE} IN SCHEMA public "
        f"REVOKE USAGE, SELECT ON SEQUENCES FROM {_APP_ROLE}"
    )
