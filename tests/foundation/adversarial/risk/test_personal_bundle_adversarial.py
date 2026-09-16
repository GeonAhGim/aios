"""U-15 DoD "번들 적대 테스트(한도 초과 REJECT·kill 자동 발동)".

애플리케이션 계층(`check_personal_order`)을 fake notifier/state 포트로
구동해, 한도 초과 시 실제로 REJECT되고 위반이 기록되며 알림이 나가는지,
일일 손실 한도 초과 시 kill switch가 자동으로 걸리고 중복 발동되지 않는지
검증한다.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from src.foundation.risk.application.evaluate_personal_order import check_personal_order
from src.foundation.risk.domain.models import PersonalRiskBundle
from src.foundation.risk.domain.rules import OrderRiskCheckInput, PersonalRiskViolation
from src.foundation.risk.ports.notifier import NotifyResult, PersonalNotification

_BUNDLE = PersonalRiskBundle(
    name="personal-conservative",
    position_pct_of_equity=Decimal("0.02"),
    daily_loss_kill_pct=Decimal("0.03"),
    max_exposure_pct=Decimal("0.30"),
    default_notional_cap_krw=Decimal("1000000"),
    symbol_whitelist=frozenset({"BTC/USDT"}),
    exchange_notional_caps={},
)


class _FakeNotifier:
    def __init__(self) -> None:
        self.sent: list[PersonalNotification] = []

    async def send(self, notification: PersonalNotification) -> NotifyResult:
        self.sent.append(notification)
        return NotifyResult(ok=True, status_code=200, error=None)


class _FakeState:
    def __init__(self) -> None:
        self.violations: list[date] = []
        self.kill_engaged = False
        self.kill_reason_value: str | None = None
        self.paper_started: date | None = None

    async def is_kill_engaged(self) -> bool:
        return self.kill_engaged

    async def kill_reason(self) -> str | None:
        return self.kill_reason_value

    async def engage_kill(self, *, reason: str) -> None:
        self.kill_engaged = True
        self.kill_reason_value = reason

    async def mark_paper_started_if_unset(self, *, today: date) -> date:
        if self.paper_started is None:
            self.paper_started = today
        return self.paper_started

    async def record_violation(self, *, occurred_on: date) -> None:
        self.violations.append(occurred_on)

    async def violation_count_since(self, since: date) -> int:
        return sum(1 for v in self.violations if v >= since)

    async def violation_count_on(self, day: date) -> int:
        return sum(1 for v in self.violations if v == day)


def _order(
    *,
    exchange: str = "bitget",
    symbol: str = "BTC/USDT",
    order_notional_krw: Decimal = Decimal("10000"),
    account_equity_krw: Decimal = Decimal("1000000"),
    current_exposure_krw: Decimal = Decimal("0"),
    daily_realized_pnl_pct: Decimal = Decimal("0"),
) -> OrderRiskCheckInput:
    return OrderRiskCheckInput(
        exchange=exchange,
        symbol=symbol,
        order_notional_krw=order_notional_krw,
        account_equity_krw=account_equity_krw,
        current_exposure_krw=current_exposure_krw,
        daily_realized_pnl_pct=daily_realized_pnl_pct,
    )


async def test_position_size_over_limit_is_rejected_and_recorded():
    notifier = _FakeNotifier()
    state = _FakeState()

    decision = await check_personal_order(
        _BUNDLE,
        _order(order_notional_krw=Decimal("50000")),
        notifier=notifier,
        state=state,
    )

    assert decision.allowed is False
    assert PersonalRiskViolation.POSITION_SIZE_EXCEEDED in decision.violations
    assert len(state.violations) == 1
    assert len(notifier.sent) == 1
    assert "Order rejected" in notifier.sent[0].message


async def test_notional_cap_exceeded_is_rejected():
    notifier = _FakeNotifier()
    state = _FakeState()

    decision = await check_personal_order(
        _BUNDLE,
        _order(order_notional_krw=Decimal("2000000"), account_equity_krw=Decimal("100000000")),
        notifier=notifier,
        state=state,
    )

    assert decision.allowed is False
    assert PersonalRiskViolation.NOTIONAL_CAP_EXCEEDED in decision.violations


async def test_daily_loss_limit_breach_auto_engages_kill_and_notifies():
    notifier = _FakeNotifier()
    state = _FakeState()

    decision = await check_personal_order(
        _BUNDLE,
        _order(daily_realized_pnl_pct=Decimal("-0.05")),
        notifier=notifier,
        state=state,
    )

    assert decision.should_kill is True
    assert state.kill_engaged is True
    assert state.kill_reason_value == "DAILY_LOSS_LIMIT_BREACHED"
    kill_notifications = [n for n in notifier.sent if n.kind.value == "KILL_SWITCH"]
    assert len(kill_notifications) == 1


async def test_kill_switch_does_not_re_engage_or_re_notify_once_active():
    notifier = _FakeNotifier()
    state = _FakeState()
    state.kill_engaged = True
    state.kill_reason_value = "DAILY_LOSS_LIMIT_BREACHED"

    await check_personal_order(
        _BUNDLE,
        _order(daily_realized_pnl_pct=Decimal("-0.10")),
        notifier=notifier,
        state=state,
    )

    kill_notifications = [n for n in notifier.sent if n.kind.value == "KILL_SWITCH"]
    assert kill_notifications == []


async def test_allowed_order_never_records_violation_or_notifies():
    notifier = _FakeNotifier()
    state = _FakeState()

    decision = await check_personal_order(_BUNDLE, _order(), notifier=notifier, state=state)

    assert decision.allowed is True
    assert state.violations == []
    assert notifier.sent == []


async def test_bulk_evaluation_stays_fast_pure_function_no_io_per_call():
    """perf 회귀 감시 — 순수 규칙 함수는 I/O가 없으므로 1000회 평가가
    수백 ms를 넘지 않아야 한다(회귀 시 domain/rules.py에 실수로 I/O가
    섞였다는 신호)."""
    import time

    from src.foundation.risk.domain.rules import evaluate_personal_order

    start = time.perf_counter()
    for _ in range(1000):
        evaluate_personal_order(_BUNDLE, _order())
    elapsed = time.perf_counter() - start

    assert elapsed < 1.0
