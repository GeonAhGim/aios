"""rls_policies_foundation — PLT-30 M5

Revision ID: b3c7f19ad2e6
Revises: 5a0aedee0af0
Create Date: 2026-09-04 00:00:00.000000

Spec: docs/specs/L4_platform_observability_tenancy_api_v1.0.md §2 M5,
§9 PLT-30, §10 리스크1.

8개 테이블 선정 근거(스펙 각주가 "foundation 8개 컨텍스트 테이블"이라고만
쓰고 표 밖에 이름을 나열하지 않는다 — 이 리프가 실제로 고른 8개): `tenant_id`
컬럼을 갖고 전량 repo 경유(`src/foundation/*/adapters/postgres_*.py`)로만
접근되는 foundation 하위 서브모듈 각각의 최상위 컨텍스트 테이블 1개씩 —
connections/evidence/mandates/paper_control/performance/reconciliation/
risk_gate/trust. 같은 서브모듈의 자식 테이블(mandate_revision,
reconciliation_item 등)은 부모 FK로 이미 간접 격리되므로 제외했다.
`ledger_account`(다른 문서 `L4_market_data_positions_ledger_v1.0.md` 소관,
WORM+역할분리로 이미 별도 방어)와 `validation`(strategies FK로 소유권을
판정하며 tenant_id 컬럼 자체가 없음)은 "foundation" 범위 밖으로 뺐다.

레거시 테이블은 §10 리스크1 결정대로 정책만 만들고 ENABLE하지 않는다 —
예시로 든 3개(orders/positions/strategy_executions)만 다룬다(전체 40여
서비스 인벤토리는 이 리프 범위 밖, 이관 시점마다 테이블 단위로 후속 리프가
ENABLE). 레거시 컬럼명은 `tenant_id`가 아니라 `user_id`이지만, PERSONAL
tenant는 `tenant_id == user_id` 불변조건(PLT-26 backfill)이 성립하므로
같은 형태의 정책을 그대로 쓸 수 있다.

`foundation_audit_event`만 `tenant_id IS NULL`(system 이벤트, 4453afe74725
docstring)을 허용한다 — `current_setting('app.role', true) = 'system'`일
때만 그 예외가 통과한다([[src/core/db/tenant_scope.py]] `system_transaction`).
"""

from collections.abc import Sequence

from alembic import op

revision: str = "b3c7f19ad2e6"
down_revision: str | Sequence[str] | None = "5a0aedee0af0"
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

_LEGACY_TABLES_POLICY_ONLY = ("orders", "positions", "strategy_executions")

_NULL_TENANT_SYSTEM_EXCEPTION = frozenset({"foundation_audit_event"})


def _policy_predicate(table: str, column: str) -> str:
    predicate = f"{column}::text = current_setting('app.tenant_id', true)"
    if table in _NULL_TENANT_SYSTEM_EXCEPTION:
        predicate = (
            f"({predicate}) OR "
            f"({column} IS NULL AND current_setting('app.role', true) = 'system')"
        )
    return predicate


def _create_policy(table: str, *, column: str = "tenant_id") -> str:
    predicate = _policy_predicate(table, column)
    return f"CREATE POLICY tenant_isolation ON {table} USING ({predicate}) WITH CHECK ({predicate})"


def upgrade() -> None:
    for table in _FOUNDATION_TABLES:
        op.execute(_create_policy(table))
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
    for table in _LEGACY_TABLES_POLICY_ONLY:
        op.execute(_create_policy(table, column="user_id"))
        # 의도적으로 ENABLE하지 않는다 — 기존 pool.acquire() 경로(트랜잭션 밖,
        # app.tenant_id 미설정)가 0행을 받아 깨지는 회귀를 막는다(§10 리스크1).


def downgrade() -> None:
    for table in _LEGACY_TABLES_POLICY_ONLY:
        op.execute(f"DROP POLICY IF EXISTS tenant_isolation ON {table}")
    for table in reversed(_FOUNDATION_TABLES):
        op.execute(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY")
        op.execute(f"DROP POLICY IF EXISTS tenant_isolation ON {table}")
