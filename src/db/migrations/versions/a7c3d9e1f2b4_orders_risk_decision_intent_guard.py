"""orders_require_risk_decision 트리거 확장 — R-56 P0 2/2: execution_ref·intent 대조(DB층).

Revision ID: a7c3d9e1f2b4
Revises: 93c0e7f6b8d9
Create Date: 2026-09-06 03:00:00.000000

Spec: docs/specs/L4_risk_and_safety_v1.0.md §4.1 I1(fingerprint 일치)·I4·
I10("ALLOW는 한 subject 전용 — 다른 intent에 이전 불가"), §3.6, §10(R-37
트리거). task-1520(51be3c7, repro 774a0a0) 재현 I10: `93c0e7f6b8d9`의 트리거는
tenant·outcome·만료만 봐서 같은 tenant의 ALLOW `decision_id`를 다른 execution·
심볼·수량 주문에 붙여도 통과했다. task-1532(ae2a452)가 앱층
`fenced_submit`에서 WORM 행을 재조회해 막았고, 이 리비전은 그 DB층 짝이다 —
앱층을 우회한 직접 INSERT/UPDATE도 같은 규칙으로 거부된다(I-10 우회불가).

`orders_require_risk_decision_guard()`를 CREATE OR REPLACE로 교체한다. 기존
검사(liquidation·cutover·tenant·outcome·만료)는 그대로이고, 결정을 참조하는
행에 대해 다음을 추가한다(cutover 무관 즉시 강제 — 이 리비전 이전에 결정을
참조한 행은 R-35/R-37 경로가 만든 것뿐이고 그 경로는 execution_ref를 항상
`exec:<execution_id>`로 기록한다):

4. `risk_decision.execution_ref`가 NULL이거나 `NEW.execution_id`가 NULL이거나
   `'exec:' || NEW.execution_id::text`와 다르면 거부. (`orders.execution_id`는
   BIGINT NULL, `execution_ref`는 VARCHAR(60) — 형식은
   `decision_binding.execution_ref_for`·`risk_inputs_assembler`와 동일.)
5. `inputs_snapshot`의 intent 키(`symbol`·`side`·`quantity` — task-1532/R-35
   `_PreSubmitInputs.model_dump(mode="json")`이 기록, quantity는 문자열)가
   주문 컬럼과 같아야 한다. 키 부재·JSON null·numeric 파싱 불가는 전부 거부
   (fail-closed, I-01) — 앱층 `decision_binding`과 같은 규칙이다. subject를
   기록하지 않은 결정은 어떤 주문에도 결속을 증명할 수 없으므로 actionable이
   아니다(I10). `orders.risk_decision_id`를 쓰는 유일한 경로(`fenced_submit`)는
   PRE_SUBMIT 결정만 참조하고 그 recorder는 세 키를 항상 넣는다 — PRE_TRADE
   (`tick_risk_phase`) 행은 주문이 참조하지 않는다. `jsonb ->> key`는 키 부재와
   JSON null 모두 SQL NULL이라 `IS DISTINCT FROM NOT NULL 컬럼`이 곧 거부다.
   `quantity`는 numeric 동치 비교(`'0.0100'` = `0.01`).

거부 문구는 §3.4 `INTEGRITY_RISK_FINGERPRINT_MISMATCH`를 재사용하고(신설
taxonomy 금지), ERRCODE는 기존과 같이 `check_violation`(23514)이다.

트리거 정의도 재생성한다: 새 검사가 `execution_id`·`symbol`·`side`·`quantity`를
읽으므로 그 컬럼의 UPDATE에도 발화해야 "일치하는 행을 넣고 나서 바꾸는"
우회가 막힌다(`src/` 어디에도 이 컬럼을 사후 UPDATE하는 경로는 없다 —
`filled_quantity`는 별개 컬럼).

downgrade는 부모 리비전 파일의 `_GUARD_FN_SQL`·`_TRIGGER_SQL`을 그대로 로드해
원 함수 본문·원 트리거 정의로 복원한다(복사본 불일치 방지).
"""
from __future__ import annotations

import importlib.util
from collections.abc import Sequence
from pathlib import Path
from types import ModuleType

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a7c3d9e1f2b4"
down_revision: str | Sequence[str] | None = "93c0e7f6b8d9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_PARENT_FILE = "93c0e7f6b8d9_orders_risk_decision_fk.py"
_GUARD_FN = "orders_require_risk_decision_guard"
_TRIGGER = "orders_require_risk_decision"
_CUTOVER_TABLE = "orders_risk_decision_cutover"
_MISMATCH = "INTEGRITY_RISK_FINGERPRINT_MISMATCH"

_GUARD_FN_SQL = f"""
CREATE OR REPLACE FUNCTION {_GUARD_FN}() RETURNS trigger AS $$
DECLARE
    v_cutover  TIMESTAMPTZ;
    v_tenant   UUID;
    v_outcome  TEXT;
    v_expires  TIMESTAMPTZ;
    v_ref      TEXT;
    v_snapshot JSONB;
    v_qty      NUMERIC;
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

    SELECT tenant_id, outcome, expires_at, execution_ref, inputs_snapshot
      INTO v_tenant, v_outcome, v_expires, v_ref, v_snapshot
      FROM risk_decision WHERE decision_id = NEW.risk_decision_id;
    IF NOT FOUND THEN
        RAISE EXCEPTION '{_TRIGGER}: risk_decision % does not exist',
            NEW.risk_decision_id USING ERRCODE = 'check_violation';
    END IF;
    IF v_tenant <> NEW.user_id THEN
        RAISE EXCEPTION '{_TRIGGER}: {_MISMATCH} decision % tenant',
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

    -- I10 (a): 결정은 정확히 한 execution에 결속된다. NULL은 어느 쪽이든 거부.
    IF v_ref IS NULL OR NEW.execution_id IS NULL
       OR v_ref <> ('exec:' || NEW.execution_id::text) THEN
        RAISE EXCEPTION '{_TRIGGER}: {_MISMATCH} decision % execution_ref',
            NEW.risk_decision_id USING ERRCODE = 'check_violation';
    END IF;

    -- I10 (b): 기록된 intent(symbol·side·quantity)가 주문 컬럼과 같아야 한다.
    -- `->>`는 키 부재·JSON null 모두 NULL → NOT NULL 컬럼과 DISTINCT → 거부(fail-closed).
    IF (v_snapshot ->> 'symbol') IS DISTINCT FROM NEW.symbol THEN
        RAISE EXCEPTION '{_TRIGGER}: {_MISMATCH} decision % symbol',
            NEW.risk_decision_id USING ERRCODE = 'check_violation';
    END IF;
    IF (v_snapshot ->> 'side') IS DISTINCT FROM NEW.side THEN
        RAISE EXCEPTION '{_TRIGGER}: {_MISMATCH} decision % side',
            NEW.risk_decision_id USING ERRCODE = 'check_violation';
    END IF;
    BEGIN
        v_qty := (v_snapshot ->> 'quantity')::numeric;  -- NULL이면 NULL
    EXCEPTION WHEN OTHERS THEN
        v_qty := NULL;  -- 파싱 불가 = 결속 증명 불가 = 거부(아래)
    END;
    IF v_qty IS NULL OR v_qty <> NEW.quantity THEN
        RAISE EXCEPTION '{_TRIGGER}: {_MISMATCH} decision % quantity',
            NEW.risk_decision_id USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql
"""  # noqa: S608 — 식별자·상수 문구만 보간(사용자 입력 없음)

_TRIGGER_SQL = (
    f"CREATE TRIGGER {_TRIGGER} "
    "BEFORE INSERT OR UPDATE OF risk_decision_id, is_liquidation, liquidation_request_id, "
    "execution_id, symbol, side, quantity "
    f"ON orders FOR EACH ROW EXECUTE FUNCTION {_GUARD_FN}()"
)


def _load_parent() -> ModuleType:
    """부모 리비전 모듈을 파일 경로로 로드한다(alembic은 리비전을 sys.modules에
    안정된 이름으로 두지 않는다). downgrade가 원문 SQL을 그대로 쓰기 위함."""
    path = Path(__file__).with_name(_PARENT_FILE)
    spec = importlib.util.spec_from_file_location(f"_parent_{down_revision}", path)
    if spec is None or spec.loader is None:  # pragma: no cover — 파일 손상 시에만
        raise RuntimeError(f"parent revision file not loadable: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def upgrade() -> None:
    op.execute(_GUARD_FN_SQL)
    op.execute(f"DROP TRIGGER IF EXISTS {_TRIGGER} ON orders")
    op.execute(_TRIGGER_SQL)


def downgrade() -> None:
    parent = _load_parent()
    op.execute(parent._GUARD_FN_SQL)  # noqa: SLF001 — 원문 복원이 목적
    op.execute(f"DROP TRIGGER IF EXISTS {_TRIGGER} ON orders")
    op.execute(parent._TRIGGER_SQL)  # noqa: SLF001
