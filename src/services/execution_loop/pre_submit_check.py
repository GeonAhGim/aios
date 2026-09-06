"""tick.py가 executor.execute() 호출 *직전*(FSM 상태 전이보다도 먼저)에
쓰는 위험 게이트 진입점 — 전수감사 §6 배선.

FSM 상태 전이(run_execution_tick의 writer 호출)보다 반드시 먼저 이 검사를
거쳐야 한다 — 전이 이후에 거부하면 그 실행이 PENDING류 상태에 영원히
갇힌다(#2026-09-02-39와 같은 클래스의 결함을 새로 만들게 된다). 거부되면
FSM은 아예 건드리지 않은 채로 이번 tick을 조용히 포기한다(다음 tick이
같은 신호를 다시 평가).

이 tick.py 레벨 검사가 "거부되면 애초에 executor.execute()를 부르지
않는다"는 안전효과를 준다. task-1715(P0-B)부터는 `Executor.execute()`
자신도 `pre_submit_gate`를 필수로 받는다(FROZEN_PAPER_ONLY 승인) — 이
검사는 그와 별개로 FSM 전이 이전 시점에 한 번 더 평가해 PENDING류 고착을
막는 용도다.

task-1717 P0-D — `evaluate_submission_gate()`가 `GateDecision` 전체(특히
`decision_id`)를 돌려준다. `Executor.execute()`가 `submit_with_fence`
경유로 전환되면서 이 결정을 그대로 전달받아야 `orders.risk_decision_id`가
채워진다(§3.6). 이 함수와 `run_execution_tick`/`is_submission_allowed`
모두 `pre_submit_gate`를 필수로 받는다(I-01, `test_gate_params_required.py`)
— "게이트가 없을 수도 있다"는 fail-open 분기는 어디에도 남기지 않는다."""

from __future__ import annotations

import logging
from collections.abc import Mapping
from decimal import Decimal
from uuid import UUID

from src.services.order_service.gate import GateDecision, GateOutcome, OrderContext, PreSubmitGate

logger = logging.getLogger(__name__)


async def evaluate_submission_gate(
    pre_submit_gate: PreSubmitGate,
    *,
    user_id: UUID,
    execution_id: int,
    exchange: str,
    mandate_revision_id: UUID | None = None,
    observed_fence: Mapping[str, int] | None = None,
    symbol: str | None = None,
    side: str | None = None,
    quantity: Decimal | None = None,
) -> GateDecision:
    """`symbol`/`side`/`quantity`(task-1717)는 실제 주문 intent 결속 키
    (I10) — 채워서 넘겨야 이 결정으로 `submit_with_fence`가 통과한다."""
    decision = await pre_submit_gate(
        OrderContext(
            user_id=user_id,
            execution_id=execution_id,
            exchange=exchange,
            mandate_revision_id=mandate_revision_id,
            observed_fence=observed_fence,
            symbol=symbol,
            side=side,
            quantity=quantity,
        )
    )
    if decision.outcome != GateOutcome.ALLOW:
        logger.info(
            "run_execution_tick(execution_id=%s): pre_submit_gate 거부(%s) — "
            "FSM은 건드리지 않고 이번 tick을 건너뜁니다.",
            execution_id,
            decision.reason_codes,
        )
    return decision


async def is_submission_allowed(
    pre_submit_gate: PreSubmitGate,
    *,
    user_id: UUID,
    execution_id: int,
    exchange: str,
    mandate_revision_id: UUID | None = None,
    observed_fence: Mapping[str, int] | None = None,
) -> bool:
    """`mandate_revision_id`는 strategy_executions에 그 컬럼이 아직 없어
    (마이그레이션 대기) 항상 None으로 호출된다 — 컬럼이 생기고
    `_load_execution_context()`의 SELECT 목록에 추가되면 호출부에서
    `execution["mandate_revision_id"]`를 그대로 넘기기만 하면 된다.

    `observed_fence`(R-36) — PRE_TRADE 등 이전 단계가 관측한 F0가 있으면
    그대로 넘겨 stale이면 거부되게 한다.

    task-1715(P0-B) — `pre_submit_gate`는 필수 인자다(I-01). 예전엔 None이면
    무조건 허용했다(fail-open) — 그 분기를 제거했다."""
    decision = await evaluate_submission_gate(
        pre_submit_gate,
        user_id=user_id,
        execution_id=execution_id,
        exchange=exchange,
        mandate_revision_id=mandate_revision_id,
        observed_fence=observed_fence,
    )
    return decision.outcome == GateOutcome.ALLOW
