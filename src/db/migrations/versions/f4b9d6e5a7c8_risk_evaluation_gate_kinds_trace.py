"""risk_evaluation gate_kind 6종 + trace_id, safety_control 멱등 digest,
strategy_executions paused_by_control_id — R-34.

Revision ID: f4b9d6e5a7c8
Revises: c7e6a3b2d4f5
Create Date: 2026-09-04 02:19:13.000000

Spec: docs/specs/L4_risk_and_safety_v1.0.md §6 표
`f4b9d6e5a7c8_risk_evaluation_gate_kinds_trace.py` 행, §3.8/§5.

`risk_evaluation.gate_kind` CHECK를 `c7d4e1a9f052`(FND-06)가 만든 두 값
(DEPLOYMENT/PRE_INTENT)에서 48번 §3 5개 게이트 전체 + DEPLOYMENT 6종으로
확장한다(§3.1 `GateKind` 열거 그대로 — 임의 추가·개명 금지). 기존 두 값의
행은 그대로 유효하므로 데이터 마이그레이션은 필요 없다.

`trace_id`는 NULL 허용 — 이 마이그레이션 이전에 쓰인 기존 행은 채울 방법이
없고(PLT-01 컨텍스트가 그 시점엔 없었음), 채우지 않는다. 신규 행은
`evaluate_risk_gate.py`가 항상 `src.core.observability.context.current()`에서
받아 기록한다(코드 책임 — DB는 강제하지 않는다, fail-closed 판단은 상위
게이트 로직에 남겨둔다).

`safety_control.idempotency_digest`는 §5 "요청 Idempotency-Key →
safety_control.idempotency_digest, UNIQUE, sha256(scope,ref,reason,actor)
24h" 멱등키 자리만 만든다 — 실제 계산·조회(ON CONFLICT 처리)는 이 리프
스콥 밖(활성화 커맨드 쪽 후속 리프)이라 NULL 허용, 값을 채우는 호출자가
아직 없다.

`strategy_executions.paused_by_control_id`는 §3.8 "정지 후 어떤 통제가
멈췄는지 추적" — legacy 실행 정지 시(`safety/legacy_execution_pauser.py`,
아직 없음) 이 컬럼에 `safety_control.id`를 남긴다. FK만 여기서 만든다.
"""
from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "f4b9d6e5a7c8"
down_revision: str | Sequence[str] | None = "c7e6a3b2d4f5"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_OLD_GATE_KINDS = ("DEPLOYMENT", "PRE_INTENT")
# §3.1 GateKind 6종 — 48번 §3 5개 게이트(DEPLOYMENT/PRE_INTENT/PRE_SUBMIT/
# INTRADAY/RECOVERY) 중 DEPLOYMENT를 제외한 나머지에 PRE_TRADE(core/risk
# 엔진 전용 게이트, §3.1)를 더한 6종. 순서·철자는 spec 원문 그대로.
_NEW_GATE_KINDS = (
    "DEPLOYMENT",
    "PRE_INTENT",
    "PRE_TRADE",
    "PRE_SUBMIT",
    "INTRADAY",
    "RECOVERY",
)


def _sql_list(values: tuple[str, ...]) -> str:
    return ", ".join(f"'{value}'" for value in values)


def upgrade() -> None:
    op.execute("ALTER TABLE risk_evaluation DROP CONSTRAINT risk_evaluation_gate_kind_check")
    op.execute(
        "ALTER TABLE risk_evaluation ADD CONSTRAINT risk_evaluation_gate_kind_check "
        f"CHECK (gate_kind IN ({_sql_list(_NEW_GATE_KINDS)}))"
    )
    op.execute("ALTER TABLE risk_evaluation ADD COLUMN trace_id UUID")

    op.execute("ALTER TABLE safety_control ADD COLUMN idempotency_digest CHAR(64) UNIQUE")

    op.execute(
        "ALTER TABLE strategy_executions ADD COLUMN paused_by_control_id UUID "
        "REFERENCES safety_control(id)"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE strategy_executions DROP COLUMN paused_by_control_id")
    op.execute("ALTER TABLE safety_control DROP COLUMN idempotency_digest")
    op.execute("ALTER TABLE risk_evaluation DROP COLUMN trace_id")
    op.execute("ALTER TABLE risk_evaluation DROP CONSTRAINT risk_evaluation_gate_kind_check")
    op.execute(
        "ALTER TABLE risk_evaluation ADD CONSTRAINT risk_evaluation_gate_kind_check "
        f"CHECK (gate_kind IN ({_sql_list(_OLD_GATE_KINDS)}))"
    )
