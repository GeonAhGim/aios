"""risk_decision — R-24 WORM(append-only) 결정 원장.

Revision ID: b8d5f2a1c3e4
Revises: a9c4e1f7b2d3
Create Date: 2026-09-04 00:00:00.000000

Spec: docs/specs/L4_risk_and_safety_v1.0.md §2.6/§9 R-24 (선행 R-02 6abb0dc
`src/core/risk/decision.py`).

I7(risk_decision은 WORM)은 R-22(`a9c4e1f7b2d3`) docstring과 동일하게
L0-3([[src/core/db/append_only.py]]) `worm_sql()`을 그대로 재사용한다 — "REVOKE는
PUBLIC만 막고 소유자를 못 막지만 트리거는 소유자에게도 예외 없이 발동한다"는
원칙 그대로다. 이 테이블은 R-22와 달리 상태 전이가 없는 순수 append-only라
`worm_sql()`의 전체 행 잠금을 그대로 쓴다(컬럼 단위로 좁힐 필요가 없다).

`risk_decision`은 이 리프에서 새로 만드는 테이블이라 `4a1d0c0de001`의
`ensure_roles_sql`이 실행 시점에 존재하던 테이블에만 `aios_app` DML 권한을
부여했다 — `ALTER DEFAULT PRIVILEGES FOR ROLE aios_migrator`는 이 저장소의
마이그레이션 실행 계정(`aios_migrator`가 아님)에는 적용되지 않는다
(`4a1d0c0de004`/`4a1d0c0de005`/`4a1d0c0de007` docstring과 동일한 미해결 R9).
그래서 `GRANT ... TO aios_app`을 이 리프가 직접 실행한 뒤 `worm_sql()`로
REVOKE(PUBLIC 대상, 방어 심화)·트리거를 얹는다.

스펙 153행 컬럼 목록에는 `RiskDecision.evidence_ref`가 없다 — evidence_ref는
결정 자체가 아니라 사후에(R-25 recorder가) 채우는 감사 참조라 이 WORM 테이블에
저장하지 않는다. `postgres_decision_repository.get()`은 항상 `evidence_ref=None`
으로 복원한다(의도된 스키마 경계, 결손 아님).

인덱스 3종은 스펙 그대로: tenant별 최신순(`list_recent`), trace_id 단건 조회
(재생/디버깅), execution_ref별 최신순(같은 실행에 대한 결정 이력).
"""
from collections.abc import Sequence

from alembic import op

from src.core.db.append_only import worm_drop_sql, worm_sql

# revision identifiers, used by Alembic.
revision: str = "b8d5f2a1c3e4"
down_revision: str | Sequence[str] | None = "a9c4e1f7b2d3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_APP_ROLE = "aios_app"

_GATE_KINDS = (
    "DEPLOYMENT",
    "PRE_INTENT",
    "PRE_TRADE",
    "PRE_SUBMIT",
    "INTRADAY",
    "RECOVERY",
)
_OUTCOMES = ("ALLOW", "DENY", "REDUCE", "PAUSE", "ESCALATE")


def _sql_list(values: tuple[str, ...]) -> str:
    return ", ".join(f"'{value}'" for value in values)


def upgrade() -> None:
    op.execute(
        f"""
        CREATE TABLE risk_decision (
            decision_id         UUID PRIMARY KEY,
            tenant_id           UUID NOT NULL REFERENCES users(user_id),
            gate_kind           VARCHAR(20) NOT NULL
                CHECK (gate_kind IN ({_sql_list(_GATE_KINDS)})),
            execution_ref       VARCHAR(60),
            subject_fingerprint CHAR(64) NOT NULL,
            outcome             VARCHAR(20) NOT NULL
                CHECK (outcome IN ({_sql_list(_OUTCOMES)})),
            reason_codes        TEXT[] NOT NULL DEFAULT '{{}}',
            obligations         TEXT[] NOT NULL DEFAULT '{{}}',
            rule_results        JSONB NOT NULL DEFAULT '[]'::jsonb,
            rule_version        VARCHAR(40) NOT NULL,
            rule_hash           CHAR(64) NOT NULL,
            engine_version      VARCHAR(40) NOT NULL,
            inputs_hash         CHAR(64) NOT NULL,
            inputs_snapshot     JSONB NOT NULL,
            input_refs          TEXT[] NOT NULL DEFAULT '{{}}',
            trace_id            UUID NOT NULL,
            evaluated_at        TIMESTAMPTZ NOT NULL,
            expires_at          TIMESTAMPTZ NOT NULL,
            latency_us          INT
        )
        """
    )
    op.execute(
        "CREATE INDEX ix_risk_decision_tenant_evaluated "
        "ON risk_decision (tenant_id, evaluated_at DESC)"
    )
    op.execute("CREATE INDEX ix_risk_decision_trace ON risk_decision (trace_id)")
    op.execute(
        "CREATE INDEX ix_risk_decision_execution_evaluated "
        "ON risk_decision (execution_ref, evaluated_at DESC)"
    )

    op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON risk_decision TO {_APP_ROLE}")

    for statement in worm_sql("risk_decision"):
        op.execute(statement)


def downgrade() -> None:
    for statement in worm_drop_sql("risk_decision"):
        op.execute(statement)
    op.execute("DROP TABLE risk_decision")
