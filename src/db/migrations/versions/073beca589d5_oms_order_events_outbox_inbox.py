"""oms_order_events_outbox_inbox — L4-06.

Revision ID: 073beca589d5
Revises: e1d9b5ed8d7d
Create Date: 2026-09-06 09:00:00.000000

Spec: docs/specs/L4_execution_oms_and_exchange_v1.0.md §2-G(`<rev1>` 행),
§4.1(I1-I7), §4.2(전이표), §5.1(쓰기별 잠금).

`orders` 신규 컬럼은 §2-C 79번 행(`_row_to_order` 목록) 그대로 — `version`만
NOT NULL DEFAULT 0, 나머지는 NULL 허용(§3.3). `scope_hash`는
`order_idempotency`(I1)에만 둔다 — `orders`에 중복하면 두 값이 갈릴 때
진실 원천이 모호해진다. `find_by_scope_hash`(L4-07)는 `order_idempotency.
order_id` 조인으로 조회한다.

cutover(`orders_risk_decision_cutover`, 93c0e7f6b8d9/a7c3d9e1f2b4와 동일
패턴): I2(전이표)·I4(터미널 불변)·I6(이벤트 동반)는 `order_repository.
transition()`(L4-07, 아직 없음) 경유로만 지켜진다 — 지금 무조건 강제하면
`order_service/repository.py`의 기존 `update_from_exchange`/
`update_after_modify`(order_events 없이 status를 바꾸는 레거시 경로,
tick.py/cancel.py/reconcile.py가 호출)가 즉시 깨진다. 그래서 이 세
불변조건은 `oms_order_transition_cutover.cutover_at` 무장 이후·
`NEW.created_at >= cutover_at`인 주문에만 적용한다(무장은 L4-09 배포 뒤
운영자가 한 문장 UPDATE로 수행). I3(filled 비감소)·I5(version 자동 증가)는
레거시도 위반할 이유가 없는 순수 안전망이라 cutover 무관 항상 강제.

I7은 `append_only.worm_sql()`을 `order_events`·`fills`에 재사용한다(둘 다
INSERT 전용, UPDATE 없음). `provider_event_inbox`는 스펙 I7 표에 있지만
§4.4가 `state: NEW -> PROCESSED|IGNORED` UPDATE를 요구해 완전 WORM을 걸면
그 전이 자체가 막힌다 — 편차: DELETE만 막고 UPDATE는 허용(task-1563 note
DoD(2)도 order_events에만 "WORM 재사용"을 명시).

tenant_id·RLS 미도입: 5개 테이블 전부 `orders.order_id`로 간접 격리된다
(`provider_event_inbox`만 매칭 전 원시 이벤트라 예외, §4.4 IGNORED).
CH-5(e1d9b5ed8d7d)·PLT-30 M5(b3c7f19ad2e6)의 "부모 FK로 이미 간접
격리되는 자식 테이블은 RLS 제외" 원칙을 따른다 — `orders` 자신도 아직
RLS를 ENABLE하지 않은 상태(`_LEGACY_TABLES_POLICY_ONLY`, §10 리스크1)라
자식 테이블에만 RLS를 앞세우면 오히려 일관성이 깨진다.
"""
from collections.abc import Sequence

from alembic import op

from src.core.db.append_only import worm_drop_sql, worm_sql

# revision identifiers, used by Alembic.
revision: str = "073beca589d5"
down_revision: str | Sequence[str] | None = "e1d9b5ed8d7d"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_APP_ROLE = "aios_app"
_CUTOVER_TABLE = "oms_order_transition_cutover"
_GUARD_FN = "oms_enforce_order_transition"
_TRIGGER = "oms_enforce_order_transition_trg"

_TERMINAL = ("FILLED", "REJECTED", "CANCELLED", "EXPIRED", "FAILED")

# §4.2 표(I2) — state_machine.py `ALLOWED`와 1:1 대응(자기 자신으로의
# 전이는 status가 안 바뀌므로 트리거 진입 전에 걸러진다, 여기 없어도 된다).
_ALLOWED_PAIRS = (
    "CREATED->VALIDATED", "CREATED->FAILED",
    "VALIDATED->SUBMITTED", "VALIDATED->FAILED",
    "SUBMITTED->ACKNOWLEDGED", "SUBMITTED->REJECTED", "SUBMITTED->UNKNOWN",
    "SUBMITTED->PARTIALLY_FILLED", "SUBMITTED->FILLED",
    "ACKNOWLEDGED->PARTIALLY_FILLED", "ACKNOWLEDGED->FILLED",
    "ACKNOWLEDGED->CANCELLED", "ACKNOWLEDGED->EXPIRED",
    "PARTIALLY_FILLED->FILLED", "PARTIALLY_FILLED->CANCELLED",
    "PARTIALLY_FILLED->EXPIRED",
    "UNKNOWN->ACKNOWLEDGED", "UNKNOWN->PARTIALLY_FILLED", "UNKNOWN->FILLED",
    "UNKNOWN->CANCELLED", "UNKNOWN->REJECTED", "UNKNOWN->FAILED",
)

_GUARD_FN_SQL = f"""
CREATE OR REPLACE FUNCTION {_GUARD_FN}() RETURNS trigger AS $$
DECLARE
    v_cutover TIMESTAMPTZ;
    v_pair    TEXT;
BEGIN
    -- I5: 버전은 호출부가 무엇을 넣었든 무시하고 항상 OLD+1로 강제한다.
    NEW.version := OLD.version + 1;

    -- I3: filled_quantity는 절대 감소하지 않는다(레거시·신규 경로 공통).
    IF NEW.filled_quantity < OLD.filled_quantity THEN
        RAISE EXCEPTION '{_TRIGGER}: filled_quantity cannot decrease (% -> %) for order %',
            OLD.filled_quantity, NEW.filled_quantity, OLD.order_id
            USING ERRCODE = 'check_violation';
    END IF;

    IF OLD.status IS DISTINCT FROM NEW.status THEN
        SELECT cutover_at INTO v_cutover FROM {_CUTOVER_TABLE} WHERE id = 1;
        IF v_cutover IS NULL OR NEW.created_at < v_cutover THEN
            RETURN NEW;  -- 레거시 주문 — cutover 비무장/이전이면 I2/I4/I6 미적용.
        END IF;

        IF OLD.status = ANY(ARRAY{list(_TERMINAL)!r}::text[]) THEN
            RAISE EXCEPTION '{_TRIGGER}: % is terminal, cannot transition to % (order %)',
                OLD.status, NEW.status, OLD.order_id USING ERRCODE = 'check_violation';
        END IF;

        v_pair := OLD.status || '->' || NEW.status;
        IF NOT (v_pair = ANY(ARRAY{list(_ALLOWED_PAIRS)!r}::text[])) THEN
            RAISE EXCEPTION '{_TRIGGER}: % is not a valid §4.2 transition (order %)',
                v_pair, OLD.order_id USING ERRCODE = 'check_violation';
        END IF;

        -- I6: 전이 UPDATE 직전 SET LOCAL oms.event_written='1' 후 order_events
        -- INSERT가 있었어야 한다(§5.1). 확인 즉시 소모해 같은 tx의 다음
        -- 전이가 재사용하지 못하게 한다.
        IF current_setting('oms.event_written', true) IS DISTINCT FROM '1' THEN
            RAISE EXCEPTION '{_TRIGGER}: status change without order_events (I6, order %)',
                OLD.order_id USING ERRCODE = 'check_violation';
        END IF;
        PERFORM set_config('oms.event_written', '0', true);
    END IF;

    RETURN NEW;
END;
$$ LANGUAGE plpgsql
"""  # noqa: S608 — 식별자·상수 문구만 보간(사용자 입력 없음)

_TRIGGER_SQL = (
    f"CREATE TRIGGER {_TRIGGER} "
    f"BEFORE UPDATE ON orders FOR EACH ROW EXECUTE FUNCTION {_GUARD_FN}()"
)

_ORDERS_NEW_COLUMNS_SQL = """
    ADD COLUMN version INTEGER NOT NULL DEFAULT 0,
    ADD COLUMN venue_symbol VARCHAR(30),
    ADD COLUMN time_in_force VARCHAR(10) NOT NULL DEFAULT 'GTC'
        CHECK (time_in_force IN ('GTC','IOC','FOK','DAY')),
    ADD COLUMN parent_order_id UUID REFERENCES orders(order_id),
    ADD COLUMN algo_run_id UUID,
    ADD COLUMN fee_total NUMERIC(30,10),
    ADD COLUMN fee_currency VARCHAR(10),
    ADD COLUMN unknown_since TIMESTAMPTZ,
    ADD COLUMN provider_order_date DATE
"""


_CUTOVER_DDL = f"""
CREATE TABLE {_CUTOVER_TABLE} (
    id SMALLINT PRIMARY KEY CHECK (id = 1), cutover_at TIMESTAMPTZ, armed_by VARCHAR(100)
)"""

_ORDER_EVENTS_DDL = """
CREATE TABLE order_events (
    seq BIGSERIAL PRIMARY KEY, order_id UUID NOT NULL REFERENCES orders(order_id),
    from_status VARCHAR(20) NOT NULL, to_status VARCHAR(20) NOT NULL, event VARCHAR(30) NOT NULL,
    reason_code VARCHAR(60), actor_subject_id VARCHAR(64) NOT NULL, trace_id UUID NOT NULL,
    command_id UUID, provider_event_id VARCHAR(100), occurred_at TIMESTAMPTZ NOT NULL,
    payload_hash CHAR(64) NOT NULL, created_at TIMESTAMPTZ NOT NULL DEFAULT now()
)"""

_OUTBOX_DDL = """
CREATE TABLE order_command_outbox (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    order_id UUID NOT NULL REFERENCES orders(order_id),
    command_type VARCHAR(10) NOT NULL CHECK (command_type IN ('SUBMIT','CANCEL','MODIFY')),
    payload JSONB NOT NULL,
    state VARCHAR(10) NOT NULL DEFAULT 'PENDING'
        CHECK (state IN ('PENDING','SENDING','DONE','RETRY','DEAD')),
    attempt INTEGER NOT NULL DEFAULT 0, not_before TIMESTAMPTZ NOT NULL DEFAULT now(),
    lease_until TIMESTAMPTZ, worker_id VARCHAR(100), last_error TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(), updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
)"""

_INBOX_DDL = """
CREATE TABLE provider_event_inbox (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(), venue VARCHAR(30) NOT NULL,
    provider_event_id VARCHAR(100) NOT NULL, venue_symbol VARCHAR(30) NOT NULL,
    exchange_order_id VARCHAR(100), client_order_id VARCHAR(100), venue_status VARCHAR(30) NOT NULL,
    filled_quantity NUMERIC(30,10) NOT NULL, average_price NUMERIC(30,10), last_fill JSONB,
    venue_ts TIMESTAMPTZ NOT NULL, received_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    source VARCHAR(20) NOT NULL CHECK (source IN ('WS','POLL','RESYNC','SUBMIT_RESPONSE')),
    raw_hash CHAR(64) NOT NULL,
    state VARCHAR(10) NOT NULL DEFAULT 'NEW' CHECK (state IN ('NEW','PROCESSED','IGNORED')),
    processed_at TIMESTAMPTZ, UNIQUE (venue, provider_event_id)
)"""

_FILLS_DDL = """
CREATE TABLE fills (
    id BIGSERIAL PRIMARY KEY, provider_fill_id VARCHAR(100) NOT NULL, venue VARCHAR(30) NOT NULL,
    order_id UUID REFERENCES orders(order_id), exchange_order_id VARCHAR(100) NOT NULL,
    symbol VARCHAR(30) NOT NULL, side VARCHAR(4) NOT NULL CHECK (side IN ('BUY','SELL')),
    quantity NUMERIC(30,10) NOT NULL, price NUMERIC(30,10) NOT NULL, fee NUMERIC(30,10) NOT NULL,
    fee_currency VARCHAR(10) NOT NULL,
    liquidity VARCHAR(10) NOT NULL CHECK (liquidity IN ('MAKER','TAKER','UNKNOWN')),
    venue_ts TIMESTAMPTZ NOT NULL, created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (venue, provider_fill_id)
)"""

_IDEMPOTENCY_DDL = """
CREATE TABLE order_idempotency (
    scope_hash CHAR(64) PRIMARY KEY, digest CHAR(64) NOT NULL,
    order_id UUID NOT NULL REFERENCES orders(order_id),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(), expires_at TIMESTAMPTZ NOT NULL
)"""


def upgrade() -> None:
    op.execute(f"ALTER TABLE orders {_ORDERS_NEW_COLUMNS_SQL}")
    op.execute(
        "ALTER TABLE orders ADD CONSTRAINT ck_orders_filled_quantity_bounds "
        "CHECK (filled_quantity >= 0 AND filled_quantity <= quantity)"
    )

    op.execute(_CUTOVER_DDL)
    op.execute(f"INSERT INTO {_CUTOVER_TABLE} (id, cutover_at) VALUES (1, NULL)")  # noqa: S608
    op.execute(_GUARD_FN_SQL)
    op.execute(_TRIGGER_SQL)

    op.execute(_ORDER_EVENTS_DDL)
    op.execute("CREATE INDEX idx_order_events_order ON order_events(order_id, seq)")
    op.execute(f"GRANT SELECT, INSERT ON order_events TO {_APP_ROLE}")
    for statement in worm_sql("order_events"):
        op.execute(statement)

    op.execute(_OUTBOX_DDL)
    op.execute(
        "CREATE INDEX idx_outbox_claim ON order_command_outbox(not_before, created_at) "
        "WHERE state = 'PENDING'"
    )
    op.execute("CREATE INDEX idx_outbox_order ON order_command_outbox(order_id)")
    op.execute(f"GRANT SELECT, INSERT, UPDATE ON order_command_outbox TO {_APP_ROLE}")

    op.execute(_INBOX_DDL)
    op.execute(
        "CREATE INDEX idx_inbox_unprocessed ON provider_event_inbox(received_at) "
        "WHERE state = 'NEW'"
    )
    op.execute(f"GRANT SELECT, INSERT, UPDATE ON provider_event_inbox TO {_APP_ROLE}")
    op.execute("REVOKE DELETE ON provider_event_inbox FROM PUBLIC")

    op.execute(_FILLS_DDL)
    op.execute("CREATE INDEX idx_fills_order ON fills(order_id) WHERE order_id IS NOT NULL")
    op.execute(f"GRANT SELECT, INSERT ON fills TO {_APP_ROLE}")
    for statement in worm_sql("fills"):
        op.execute(statement)

    op.execute(_IDEMPOTENCY_DDL)
    op.execute("CREATE INDEX idx_order_idempotency_expires ON order_idempotency(expires_at)")
    op.execute(f"GRANT SELECT, INSERT, DELETE ON order_idempotency TO {_APP_ROLE}")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS order_idempotency")

    for statement in worm_drop_sql("fills"):
        op.execute(statement)
    op.execute("DROP TABLE IF EXISTS fills")

    op.execute("DROP TABLE IF EXISTS provider_event_inbox")
    op.execute("DROP TABLE IF EXISTS order_command_outbox")

    for statement in worm_drop_sql("order_events"):
        op.execute(statement)
    op.execute("DROP TABLE IF EXISTS order_events")

    op.execute(f"DROP TRIGGER IF EXISTS {_TRIGGER} ON orders")
    op.execute(f"DROP FUNCTION IF EXISTS {_GUARD_FN}()")
    op.execute(f"DROP TABLE IF EXISTS {_CUTOVER_TABLE}")

    op.execute("ALTER TABLE orders DROP CONSTRAINT ck_orders_filled_quantity_bounds")
    op.execute(
        "ALTER TABLE orders "
        "DROP COLUMN version, DROP COLUMN venue_symbol, DROP COLUMN time_in_force, "
        "DROP COLUMN parent_order_id, DROP COLUMN algo_run_id, DROP COLUMN fee_total, "
        "DROP COLUMN fee_currency, DROP COLUMN unknown_since, DROP COLUMN provider_order_date"
    )
