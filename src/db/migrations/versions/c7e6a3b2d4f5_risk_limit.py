"""risk_limit + risk_limit_breach — R-26 노출 한도 저장소.

Revision ID: c7e6a3b2d4f5
Revises: b8d5f2a1c3e4
Create Date: 2026-09-04 00:00:00.000000

Spec: docs/specs/L4_risk_and_safety_v1.0.md §2.6/§9 R-26 (선행 R-14).

표현식 UNIQUE `ux_risk_limit_scope`는 `COALESCE(tenant_id, '00000000…')`로
tenant_id NULL(플랫폼 기본값)도 다른 tenant 행과 동일하게 "한 (scope,
scope_ref, metric) 조합당 한 행"을 강제한다 — 일반 `UNIQUE(tenant_id, ...)`는
Postgres에서 `NULL <> NULL`이라 플랫폼 기본값 행이 무제한으로 중복 생성될 수
있었다. `postgres_limit_repository.upsert()`는 이 인덱스를 `ON CONFLICT`
충돌 대상으로 그대로 재사용한다(§6 표 "risk_limit upsert" 행) — 인덱스
표현식과 `ON CONFLICT` 절의 표현식이 토씨 하나까지 같아야 Postgres가 같은
인덱스로 추론하므로, 이 마이그레이션과 리포지토리 코드는 함께 바뀌어야 한다.

`risk_limit_breach.decision_id`는 R-24(`b8d5f2a1c3e4`)가 만든
`risk_decision.decision_id`를 참조한다 — 이번 PM 사이클에서 이 리프가
마이그레이션 체인의 유일한 진행 중 리프라 head가 그대로 R-24다(착수 시점
`alembic heads` 단일 확인, task note 참고).

한도 기본값 시드는 넣지 않는다 — §10에서 Risk officer 미승인(Draft) 상태로
남아 있다(task decision 참고).
"""
from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c7e6a3b2d4f5"
down_revision: str | Sequence[str] | None = "b8d5f2a1c3e4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_APP_ROLE = "aios_app"

_SCOPES = ("TENANT", "ACCOUNT", "STRATEGY", "SYMBOL", "ASSET_CLASS", "PROVIDER")
_METRICS = (
    "GROSS_NOTIONAL_PCT",
    "NET_NOTIONAL_PCT",
    "MAX_ORDER_NOTIONAL",
    "MAX_OPEN_POSITIONS",
    "MAX_TRADES_PER_HOUR",
    "MAX_LEVERAGE",
)
_SEVERITIES = ("WARN", "CRITICAL")

_NIL_UUID = "00000000-0000-0000-0000-000000000000"

# 표현식 UNIQUE와 upsert()의 ON CONFLICT가 문자 그대로 공유하는 표현식.
_CONFLICT_EXPR = (
    f"COALESCE(tenant_id, '{_NIL_UUID}'::uuid), scope, scope_ref, metric"
)


def _sql_list(values: tuple[str, ...]) -> str:
    return ", ".join(f"'{value}'" for value in values)


def upgrade() -> None:
    op.execute(
        f"""
        CREATE TABLE risk_limit (
            id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id      UUID REFERENCES users(user_id),
            scope          VARCHAR(20) NOT NULL
                CHECK (scope IN ({_sql_list(_SCOPES)})),
            scope_ref      VARCHAR(200) NOT NULL,
            metric         VARCHAR(30) NOT NULL
                CHECK (metric IN ({_sql_list(_METRICS)})),
            limit_value    NUMERIC(30, 10) NOT NULL CHECK (limit_value >= 0),
            hard           BOOLEAN NOT NULL DEFAULT TRUE,
            effective_from TIMESTAMPTZ,
            effective_to   TIMESTAMPTZ,
            created_by     UUID,
            approval_ref   TEXT,
            updated_at     TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    op.execute(f"CREATE UNIQUE INDEX ux_risk_limit_scope ON risk_limit ({_CONFLICT_EXPR})")
    op.execute(
        "CREATE INDEX ix_risk_limit_scope_ref ON risk_limit (scope, scope_ref)"
    )

    op.execute(
        f"""
        CREATE TABLE risk_limit_breach (
            id           BIGSERIAL PRIMARY KEY,
            limit_id     UUID NOT NULL REFERENCES risk_limit(id),
            decision_id  UUID NOT NULL REFERENCES risk_decision(decision_id),
            observed     NUMERIC NOT NULL,
            limit_value  NUMERIC NOT NULL,
            severity     VARCHAR(20) NOT NULL
                CHECK (severity IN ({_sql_list(_SEVERITIES)})),
            occurred_at  TIMESTAMPTZ NOT NULL
        )
        """
    )
    op.execute(
        "CREATE INDEX ix_risk_limit_breach_limit ON risk_limit_breach "
        "(limit_id, occurred_at DESC)"
    )

    op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON risk_limit TO {_APP_ROLE}")
    op.execute(f"GRANT SELECT, INSERT ON risk_limit_breach TO {_APP_ROLE}")
    op.execute(f"GRANT USAGE, SELECT ON risk_limit_breach_id_seq TO {_APP_ROLE}")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS risk_limit_breach")
    op.execute("DROP TABLE IF EXISTS risk_limit")
