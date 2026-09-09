"""orders_cancel_requested_status -- L4-06 §9 promotes CANCEL_REQUESTED to a
real OrderStatus member (task-2432, closes task-2406 DoD(e) gap).

Revision ID: f93d241b4ab6
Revises: f5529244403f
Create Date: 2026-09-09 12:40:00.000000

Spec: docs/specs/execution_oms_and_exchange.md#§2-C 상태기계 표 · §9 L4-06 +
ibor_fund_accounting_and_resilience(§9 FA-16 replay_verify).

`src/services/safety/open_order_sweeper.py`'s kill-switch cancel path
(R-39) has always written the literal string `'CANCEL_REQUESTED'` straight
into `orders.status` -- a value that was never a member of L4-06's frozen
`OrderStatus` contract (`src/data/models/trading.py`). Two consequences,
both documented as an accepted gap in that module's docstring until this
leaf: (1) `073beca589d5`'s `oms_enforce_order_transition` guard function
has no `_ALLOWED_PAIRS` entry for it, so once `oms_order_transition_cutover`
is armed, every real cancel-sweep write will start failing I2; (2) the
sweeper could not record a real `order_events` transition (`to_status`
would crash `PostgresOrderEventRepository._row_to_event()`'s `OrderStatus(...)`
cast, which `scripts/replay_verify.py` calls for every order it touches),
so it fell back to a self-loop event (`from_status == to_status`) that kept
`replay_verify` from crashing but left the orders projection permanently
diverged from the real row for any swept order.

This migration is purely a `CREATE OR REPLACE FUNCTION` of
`oms_enforce_order_transition` (073beca589d5) that adds six pairs to
`_ALLOWED_PAIRS` -- the entry edges `SUBMITTED|ACKNOWLEDGED|
PARTIALLY_FILLED -> CANCEL_REQUESTED` and the exit edges
`CANCEL_REQUESTED -> CANCELLED|FILLED|PARTIALLY_FILLED` -- mirroring the
same six additions made to `state_machine.ALLOWED` (L4-02) in the same
commit. No DDL on `orders.status` itself: that column has never been a
Postgres ENUM (it is `VARCHAR`, enforced only by this trigger function and
the Python-side `OrderStatus` contract), so there is no type to widen.

`_TERMINAL`/the trigger definition itself are unchanged -- `CANCEL_REQUESTED`
is not terminal (orders in it still resolve to CANCELLED/FILLED/
PARTIALLY_FILLED via reconcile), so it needs no `_TERMINAL` entry, and the
trigger's `BEFORE UPDATE ON orders FOR EACH ROW EXECUTE FUNCTION
oms_enforce_order_transition()` definition does not reference the pairs
list, so it does not need to be dropped and recreated (unlike
a7c3d9e1f2b4, which also had to widen its trigger's `UPDATE OF <columns>`
clause -- this trigger already fires on every `UPDATE ON orders`).

This is deliberately the only migration in this leaf (task-2432's decision:
"한 사이클 한 개 원칙에 따라 이 리프 외 마이그레이션 금지") -- landed at the
tail of the already-open migration chain (1942->1943->2351->2121, 2357),
confirmed by a single `alembic heads` (`c7f1e3a9d024`) at the start of this
task. `f5529244403f` (RD-4, task-2463) landed on `main` from a concurrent
worker while this leaf was in flight, also declaring `c7f1e3a9d024` as its
`down_revision` and forking `heads` to two -- this file's `down_revision`
was retargeted from `c7f1e3a9d024` to `f5529244403f` to re-serialize onto
the new tip (a plain single-parent rebase, not a merge revision, so the
task's "직접 merge revision 만들지 말 것" instruction still holds).

downgrade loads 073beca589d5's own `_GUARD_FN_SQL` by file path (same
pattern as a7c3d9e1f2b4) and re-executes it verbatim, restoring the
original (six-pairs-fewer) function body -- no hand-copied duplicate to
drift out of sync.
"""
from __future__ import annotations

import importlib.util
from collections.abc import Sequence
from pathlib import Path
from types import ModuleType

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "f93d241b4ab6"
down_revision: str | Sequence[str] | None = "f5529244403f"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_PARENT_FILE = "073beca589d5_oms_order_events_outbox_inbox.py"
_GUARD_FN = "oms_enforce_order_transition"
_TRIGGER = "oms_enforce_order_transition_trg"
_CUTOVER_TABLE = "oms_order_transition_cutover"

_TERMINAL = ("FILLED", "REJECTED", "CANCELLED", "EXPIRED", "FAILED")

# 073beca589d5's _ALLOWED_PAIRS plus the six CANCEL_REQUESTED edges (L4-02
# state_machine.ALLOWED mirrors these exactly, same commit).
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
    "SUBMITTED->CANCEL_REQUESTED", "ACKNOWLEDGED->CANCEL_REQUESTED",
    "PARTIALLY_FILLED->CANCEL_REQUESTED",
    "CANCEL_REQUESTED->CANCELLED", "CANCEL_REQUESTED->FILLED",
    "CANCEL_REQUESTED->PARTIALLY_FILLED",
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


def _load_parent() -> ModuleType:
    """073beca589d5 모듈을 파일 경로로 로드해 원문 `_GUARD_FN_SQL`을 그대로
    재실행한다(downgrade가 복사본을 따로 들고 있다가 원본과 드리프트하는
    것을 막는다 — a7c3d9e1f2b4와 동일 관례)."""
    path = Path(__file__).with_name(_PARENT_FILE)
    spec = importlib.util.spec_from_file_location(f"_parent_{down_revision}", path)
    if spec is None or spec.loader is None:  # pragma: no cover — 파일 손상 시에만
        raise RuntimeError(f"parent revision file not loadable: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def upgrade() -> None:
    op.execute(_GUARD_FN_SQL)


def downgrade() -> None:
    parent = _load_parent()
    op.execute(parent._GUARD_FN_SQL)  # noqa: SLF001 — 원문 복원이 목적
