"""foundation_risk_gate — FND-06 Risk & Safety Gate(deterministic veto + kill switch)

Revision ID: c7d4e1a9f052
Revises: a1f3c9d6b8e2
Create Date: 2026-09-02 00:00:00.000000

병합 참고: 이 리비전은 origin/main에 실제로 커밋된 마지막 리비전
(a1f3c9d6b8e2, FND-05)을 기준으로 체인했다 — 커밋 시점에 동시 세션의
`3b244535b311`(FND-04 validation)이 아직 uncommitted였기 때문(먼저 커밋된
쪽에 체인하면 origin에 없는 리비전을 가리키게 되는 위험을 피함). 두
리비전이 모두 커밋된 뒤 alembic이 multiple heads를 보고하면 표준
`alembic merge` 리비전으로 합친다.

Spec: AIOSproject 48_risk_safety_gate_and_kill_switch_specification_v1.0.md,
78_risk_safety_l3_build_and_operational_specification_v1.0.md,
71_mihwa_aios_foundation_implementation_work_packages_v1.0.md FND-06.

스콥 축소(명시, FND-01/02/05와 같은 원칙):
- 71번 §3 FND-06 최소 산출물은 "deterministic RiskDecision과 deployment/
  pre-intent checks"다 — 48번 §3의 pre-submit/intraday/recovery 게이트는
  실제 주문 제출 경로(FND-07 paper_control, order adapter)가 있어야
  의미가 있어 만들지 않는다. `risk_evaluation.gate_kind`는 DEPLOYMENT/
  PRE_INTENT 두 값만 갖는다.
- `risk_rule_bundle`(78번 §1) 테이블은 만들지 않는다 — 지금은 규칙이
  코드(domain/rules.py::RULE_VERSION)에 고정된 상수이고, mandates의
  compiler_version()과 동일한 이유로 실제 규칙 발행/버전관리 워크플로가
  아직 없다. `risk_evaluation.rule_version`은 그 코드 상수를 그대로 찍는다.
- `risk_signal`(78번 §1, drift/staleness 신호 수집)은 만들지 않는다 —
  intraday monitor(게이트 4) 스콥이라 이 리프 밖.
- kill switch(`safety_control`)는 이 마이그레이션이 만드는 새 Foundation
  전용 개념이다 — 기존 실행 엔진(`src/services/execution_loop/`,
  `src/services/order_service/`, 레드팀 감사 #08 대상)의 kill switch를
  대체하거나 수정하지 않는다. 이 리프의 안전 통제는 아직 실제 주문
  제출 경로에 배선돼 있지 않다(71번 §1 FROZEN 영역 미변경).
"""
from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c7d4e1a9f052"
down_revision: str | None = "a1f3c9d6b8e2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 78번 §3 "ActivateSafetyControl transaction increments target fence
    # token" — (scope, scope_ref) 하나당 단조증가 카운터 하나. GLOBAL처럼
    # scope_ref가 없는 경우 빈 문자열을 쓴다(domain/models.py::GLOBAL_SCOPE_REF
    # — NULL을 PK에 쓰면 Postgres UNIQUE가 "NULL != NULL"이라 중복을 못 막는다).
    op.execute(
        """
        CREATE TABLE safety_fence (
            scope         VARCHAR(30) NOT NULL,
            scope_ref     VARCHAR(200) NOT NULL DEFAULT '',
            current_token BIGINT NOT NULL DEFAULT 0,
            PRIMARY KEY (scope, scope_ref)
        )
        """
    )

    op.execute(
        """
        CREATE TABLE safety_control (
            id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            scope             VARCHAR(30) NOT NULL
                CHECK (scope IN ('GLOBAL','TENANT','ACCOUNT','STRATEGY_DEPLOYMENT','PROVIDER')),
            scope_ref         VARCHAR(200) NOT NULL DEFAULT '',
            state             VARCHAR(20) NOT NULL DEFAULT 'ACTIVE'
                CHECK (state IN ('ACTIVE','INACTIVE')),
            reason            TEXT NOT NULL,
            actor_subject_id  UUID NOT NULL REFERENCES users(user_id),
            fence_token       BIGINT NOT NULL,
            created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
            deactivated_at    TIMESTAMPTZ
        )
        """
    )
    op.execute(
        "CREATE INDEX ix_safety_control_scope_ref ON safety_control (scope, scope_ref, state)"
    )

    op.execute(
        """
        CREATE TABLE risk_evaluation (
            id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id           UUID NOT NULL REFERENCES users(user_id),
            gate_kind           VARCHAR(20) NOT NULL
                CHECK (gate_kind IN ('DEPLOYMENT','PRE_INTENT')),
            subject_fingerprint VARCHAR(128) NOT NULL,
            outcome             VARCHAR(20) NOT NULL
                CHECK (outcome IN ('ALLOW','DENY','REDUCE','PAUSE','ESCALATE')),
            reason_codes        TEXT[] NOT NULL DEFAULT '{}',
            obligations         TEXT[] NOT NULL DEFAULT '{}',
            rule_version        VARCHAR(20) NOT NULL,
            evaluated_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
            expires_at          TIMESTAMPTZ
        )
        """
    )
    op.execute(
        "CREATE INDEX ix_risk_evaluation_fingerprint ON risk_evaluation "
        "(tenant_id, subject_fingerprint)"
    )


def downgrade() -> None:
    op.execute("DROP TABLE risk_evaluation")
    op.execute("DROP TABLE safety_control")
    op.execute("DROP TABLE safety_fence")
