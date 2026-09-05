"""orders.risk_decision_id FK + orders_require_risk_decision 트리거 — R-37.

Revision ID: 93c0e7f6b8d9
Revises: f4b9d6e5a7c8
Create Date: 2026-09-05 03:00:00.000000

Spec: docs/specs/L4_risk_and_safety_v1.0.md §2.6 표
`93c0e7f6b8d9_orders_risk_decision_fk.py` 행, §4.1 I1(Master Authority),
§10 "`orders` 트리거 cutover" 행.

I1 — "`orders` 행은 유효(만료 전·fingerprint 일치)한 ALLOW/REDUCE
`risk_decision`을 참조하거나 `liquidation_request`를 참조한다"를 DB가
강제한다(코드 경로 우회 불가, I-10). 트리거 `orders_require_risk_decision`
(BEFORE INSERT OR UPDATE OF 관련 컬럼)의 규칙:

1. `is_liquidation = TRUE`면 `liquidation_request_id`가 반드시 있어야 한다.
   (`liquidation_request` 테이블은 `e5a8c5d4f6b7`(R-40 계열)이 아직 만들지
   않았으므로 스펙 원문대로 FK 없는 `UUID NULL` 컬럼만 둔다.)
2. `risk_decision_id`가 있으면 그 결정은 (a) 같은 tenant(`orders.user_id`)의
   것이고 (b) outcome이 ALLOW/REDUCE이며 (c) `created_at < expires_at`이어야
   한다. 이 세 검사는 cutover와 무관하게 **즉시** 강제된다 — 이 마이그레이션
   이전 행은 결정을 참조한 적이 없어 호환성 문제가 없다.
3. `risk_decision_id`가 NULL이면 `orders_risk_decision_cutover.cutover_at`이
   설정돼 있고 `created_at >= cutover_at`일 때만 거부한다.

cutover 처리(§10, PM 결정 사항): 스펙은 "cutover_at은 마이그레이션 적용
시각"이라 했지만 같은 §10 행이 "R-32 배포 **후** R-37 적용(순서 고정)"을
전제한다. 이 리프 착수 시점(2026-09-05)에 `src/` 어디에도
`risk_decision_id`를 쓰는 호출자가 없다(R-32 `tick_risk_phase` 미머지) —
적용 시각으로 무장하면 모든 실서비스 주문 INSERT가 이 트리거에서 즉시
실패한다. 그래서 cutover는 단일 행 테이블 `orders_risk_decision_cutover`
(`id = 1`)에 **NULL(비무장)** 로 두고, 무장은 R-32 배포 뒤 운영자가 아래
한 문장으로 수행한다(단조: 이미 무장됐으면 no-op, 105번 조건부 UPDATE):

    UPDATE orders_risk_decision_cutover
       SET cutover_at = now(), armed_by = '<operator>'
     WHERE id = 1 AND cutover_at IS NULL;

`tests/adversarial/risk/test_fence_race.py`가 트랜잭션 안에서 무장한 뒤
롤백하는 방식으로 "무장 시 결정 없는 INSERT 거부"를 증명한다.

RAISE는 ERRCODE `check_violation`(23514)으로 던진다 — asyncpg에서
`CheckViolationError`로 잡혀 호출부가 다른 무결성 오류와 구분할 수 있다.
"""
from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "93c0e7f6b8d9"
down_revision: str | Sequence[str] | None = "f4b9d6e5a7c8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_GUARD_FN = "orders_require_risk_decision_guard"
_TRIGGER = "orders_require_risk_decision"
_CUTOVER_TABLE = "orders_risk_decision_cutover"

_GUARD_FN_SQL = f"""
CREATE OR REPLACE FUNCTION {_GUARD_FN}() RETURNS trigger AS $$
DECLARE
    v_cutover  TIMESTAMPTZ;
    v_tenant   UUID;
    v_outcome  TEXT;
    v_expires  TIMESTAMPTZ;
BEGIN
    IF NEW.is_liquidation THEN
        IF NEW.liquidation_request_id IS NULL THEN
            RAISE EXCEPTION '{_TRIGGER}: liquidation order % requires liquidation_request_id',
                NEW.order_id USING ERRCODE = 'check_violation';
        END IF;
        RETURN NEW;
    END IF;

    IF NEW.risk_decision_id IS NULL THEN
        SELECT cutover_at INTO v_cutover FROM {_CUTOVER_TABLE} WHERE id = 1;
        IF v_cutover IS NOT NULL AND NEW.created_at >= v_cutover THEN
            RAISE EXCEPTION '{_TRIGGER}: order % created at % (cutover %) has no risk_decision_id',
                NEW.order_id, NEW.created_at, v_cutover USING ERRCODE = 'check_violation';
        END IF;
        RETURN NEW;
    END IF;

    SELECT tenant_id, outcome, expires_at INTO v_tenant, v_outcome, v_expires
      FROM risk_decision WHERE decision_id = NEW.risk_decision_id;
    IF NOT FOUND THEN
        RAISE EXCEPTION '{_TRIGGER}: risk_decision % does not exist',
            NEW.risk_decision_id USING ERRCODE = 'check_violation';
    END IF;
    IF v_tenant <> NEW.user_id THEN
        RAISE EXCEPTION '{_TRIGGER}: INTEGRITY_RISK_FINGERPRINT_MISMATCH decision % tenant',
            NEW.risk_decision_id USING ERRCODE = 'check_violation';
    END IF;
    IF v_outcome NOT IN ('ALLOW', 'REDUCE') THEN
        RAISE EXCEPTION '{_TRIGGER}: decision % outcome % is not actionable',
            NEW.risk_decision_id, v_outcome USING ERRCODE = 'check_violation';
    END IF;
    IF NEW.created_at >= v_expires THEN
        RAISE EXCEPTION '{_TRIGGER}: RISK_DECISION_EXPIRED decision % expired at %',
            NEW.risk_decision_id, v_expires USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql
"""  # noqa: S608 — 식별자 상수만 보간(사용자 입력 없음)

_TRIGGER_SQL = (
    f"CREATE TRIGGER {_TRIGGER} "
    "BEFORE INSERT OR UPDATE OF risk_decision_id, is_liquidation, liquidation_request_id "
    f"ON orders FOR EACH ROW EXECUTE FUNCTION {_GUARD_FN}()"
)


def upgrade() -> None:
    op.execute(
        "ALTER TABLE orders ADD COLUMN risk_decision_id UUID "
        "REFERENCES risk_decision(decision_id)"
    )
    op.execute("ALTER TABLE orders ADD COLUMN liquidation_request_id UUID")
    op.execute(
        "CREATE INDEX idx_orders_risk_decision ON orders(risk_decision_id) "
        "WHERE risk_decision_id IS NOT NULL"
    )
    op.execute(
        f"""
        CREATE TABLE {_CUTOVER_TABLE} (
            id          SMALLINT PRIMARY KEY CHECK (id = 1),
            cutover_at  TIMESTAMPTZ,
            armed_by    VARCHAR(100),
            note        TEXT
        )
        """
    )
    op.execute(f"INSERT INTO {_CUTOVER_TABLE} (id, cutover_at) VALUES (1, NULL)")  # noqa: S608
    op.execute(_GUARD_FN_SQL)
    op.execute(_TRIGGER_SQL)


def downgrade() -> None:
    op.execute(f"DROP TRIGGER IF EXISTS {_TRIGGER} ON orders")
    op.execute(f"DROP FUNCTION IF EXISTS {_GUARD_FN}()")
    op.execute(f"DROP TABLE IF EXISTS {_CUTOVER_TABLE}")
    op.execute("DROP INDEX IF EXISTS idx_orders_risk_decision")
    op.execute("ALTER TABLE orders DROP COLUMN liquidation_request_id")
    op.execute("ALTER TABLE orders DROP COLUMN risk_decision_id")
