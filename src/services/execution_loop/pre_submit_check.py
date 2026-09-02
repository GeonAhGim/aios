"""tick.py가 executor.execute() 호출 *직전*(FSM 상태 전이보다도 먼저)에
쓰는 위험 게이트 진입점 — 전수감사 §6 배선.

FSM 상태 전이(run_execution_tick의 writer 호출)보다 반드시 먼저 이 검사를
거쳐야 한다 — 전이 이후에 거부하면 그 실행이 PENDING류 상태에 영원히
갇힌다(#2026-09-02-39와 같은 클래스의 결함을 새로 만들게 된다). 거부되면
FSM은 아예 건드리지 않은 채로 이번 tick을 조용히 포기한다(다음 tick이
같은 신호를 다시 평가).

`src/core/executor/executor.py`는 FROZEN_PAPER_ONLY라 시그니처를 바꿔
pre_submit_gate를 그 안까지 관통시키지 않는다 — 이 tick.py 레벨 검사
하나로 "거부되면 애초에 executor.execute()를 부르지 않는다"는 동일한
안전효과를 얻는다."""
from __future__ import annotations

import logging
from uuid import UUID

from src.services.order_service.gate import GateOutcome, OrderContext, PreSubmitGate

logger = logging.getLogger(__name__)


async def is_submission_allowed(
    pre_submit_gate: PreSubmitGate | None,
    *,
    user_id: UUID,
    execution_id: int,
    exchange: str,
    mandate_revision_id: UUID | None = None,
) -> bool:
    """`mandate_revision_id`는 strategy_executions에 그 컬럼이 아직 없어
    (마이그레이션 대기) 항상 None으로 호출된다 — 컬럼이 생기고
    `_load_execution_context()`의 SELECT 목록에 추가되면 호출부에서
    `execution["mandate_revision_id"]`를 그대로 넘기기만 하면 된다."""
    if pre_submit_gate is None:
        return True

    decision = await pre_submit_gate(
        OrderContext(
            user_id=user_id,
            execution_id=execution_id,
            exchange=exchange,
            mandate_revision_id=mandate_revision_id,
        )
    )
    if decision.outcome != GateOutcome.ALLOW:
        logger.info(
            "run_execution_tick(execution_id=%s): pre_submit_gate 거부(%s) — "
            "FSM은 건드리지 않고 이번 tick을 건너뜁니다.",
            execution_id,
            decision.reason_codes,
        )
        return False
    return True
