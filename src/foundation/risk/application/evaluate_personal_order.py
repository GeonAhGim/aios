"""U-15 주문 리스크 게이트 애플리케이션 계층 — 순수 domain 판정에 알림/kill
상태 갱신 I/O를 얹는다.

Spec: task-2749 DoD "번들 적대 테스트(한도 초과 REJECT·kill 자동 발동)".
"""

from __future__ import annotations

from datetime import datetime, timezone

from src.foundation.risk.domain.models import PersonalRiskBundle
from src.foundation.risk.domain.rules import (
    OrderRiskCheckInput,
    OrderRiskDecision,
    evaluate_personal_order,
)
from src.foundation.risk.ports.notifier import (
    PersonalNotification,
    PersonalNotificationKind,
    PersonalNotifierPort,
)
from src.foundation.risk.ports.state import PersonalOperationStatePort


async def check_personal_order(
    bundle: PersonalRiskBundle,
    order: OrderRiskCheckInput,
    *,
    notifier: PersonalNotifierPort,
    state: PersonalOperationStatePort,
) -> OrderRiskDecision:
    """한도 위반이면 위반 이력을 기록하고 알림을 보낸다. 일일 손실 한도가
    깨졌으면(이미 kill이 걸려 있지 않은 한) kill switch를 발동하고 별도
    알림을 보낸다 — 두 알림은 서로 다른 사유라 합치지 않는다."""
    decision = evaluate_personal_order(bundle, order)

    if decision.violations:
        reasons = ", ".join(v.value for v in decision.violations)
        await state.record_violation(occurred_on=datetime.now(timezone.utc).date())
        await notifier.send(
            PersonalNotification(
                kind=PersonalNotificationKind.LIMIT_BREACH,
                message=f"{order.exchange}:{order.symbol} 주문 거부 — {reasons}",
            )
        )

    if decision.should_kill and not await state.is_kill_engaged():
        await state.engage_kill(reason="DAILY_LOSS_LIMIT_BREACHED")
        await notifier.send(
            PersonalNotification(
                kind=PersonalNotificationKind.KILL_SWITCH,
                message="일일 손실 한도 초과로 자동 kill 발동",
            )
        )

    return decision
