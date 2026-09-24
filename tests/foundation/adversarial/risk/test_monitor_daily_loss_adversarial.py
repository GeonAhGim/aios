"""task-6510 (XREV task-3820 REJECT, finding id=149) 적대 테스트 —
`check_personal_daily_loss`(주문 없는 상시 kill 경로)가 D2/D3 요구사항을
만족하는지: 3% 일일 손실 breach 시 kill 자동 발동 + 알림, 이미 발동된
상태에서는 중복 발동/알림 없음(idempotent), 손실 미만이면 어떤 side effect도
없음, equity<=0(신뢰 불가능한 분모)이면 fail-safe하게 아무것도 안 건드림,
경계값(정확히 -3%)은 `<=`라서 kill이 걸린다.

INVARIANTS.md I-10 cross-check: 이 leaf의 목표 자체가 "safety component가
배선만 돼 있고 실제로 호출되지 않는" 결함(I-10 위반)을 고치는 것이므로,
아래 테스트들은 order 없이(=이 leaf가 새로 만든 상시 호출 경로만으로) kill이
실제로 발동함을 증명한다 — `evaluate_personal_order`를 우회하지 않는다.
"""

from __future__ import annotations

from decimal import Decimal

from src.foundation.risk.application.monitor_daily_loss import check_personal_daily_loss
from src.foundation.risk.domain.models import PersonalRiskBundle
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
    def __init__(self, *, kill_engaged: bool = False) -> None:
        self.kill_engaged = kill_engaged
        self.kill_reason_value: str | None = None
        self.engage_calls = 0

    async def is_kill_engaged(self) -> bool:
        return self.kill_engaged

    async def kill_reason(self) -> str | None:
        return self.kill_reason_value

    async def engage_kill(self, *, reason: str) -> None:
        self.engage_calls += 1
        self.kill_engaged = True
        self.kill_reason_value = reason


async def test_daily_loss_breach_engages_kill_and_notifies():
    notifier = _FakeNotifier()
    state = _FakeState()

    engaged = await check_personal_daily_loss(
        _BUNDLE,
        account_equity_krw=Decimal("1000000"),
        daily_realized_pnl_pct=Decimal("-0.05"),
        notifier=notifier,
        state=state,
    )

    assert engaged is True
    assert state.kill_engaged is True
    assert state.kill_reason_value == "DAILY_LOSS_LIMIT_BREACHED"
    kill_notifications = [n for n in notifier.sent if n.kind.value == "KILL_SWITCH"]
    assert len(kill_notifications) == 1


async def test_no_breach_leaves_no_side_effects():
    """negative — 3% 미만 손실은 kill도, 알림도, state 기록도 남기지 않는다."""
    notifier = _FakeNotifier()
    state = _FakeState()

    engaged = await check_personal_daily_loss(
        _BUNDLE,
        account_equity_krw=Decimal("1000000"),
        daily_realized_pnl_pct=Decimal("-0.02"),
        notifier=notifier,
        state=state,
    )

    assert engaged is False
    assert state.kill_engaged is False
    assert notifier.sent == []


async def test_already_engaged_is_idempotent_no_duplicate_notify():
    """negative — 이미 kill이 걸려 있으면 재호출해도 engage_kill/notify가
    다시 나가지 않는다(매 30초 tick마다 중복 알림 스팸 방지)."""
    notifier = _FakeNotifier()
    state = _FakeState(kill_engaged=True)
    state.kill_reason_value = "DAILY_LOSS_LIMIT_BREACHED"

    engaged = await check_personal_daily_loss(
        _BUNDLE,
        account_equity_krw=Decimal("1000000"),
        daily_realized_pnl_pct=Decimal("-0.10"),
        notifier=notifier,
        state=state,
    )

    assert engaged is True
    assert state.engage_calls == 0
    assert notifier.sent == []


async def test_non_positive_equity_is_fail_safe_not_fail_closed():
    """negative — equity<=0이면 비율의 분모를 신뢰할 수 없으므로 kill을
    추측으로 걸지 않고 아무 것도 건드리지 않는다(주문 경로의 fail-closed와
    달리, 이 상시 모니터는 거부할 주문이 없다)."""
    notifier = _FakeNotifier()
    state = _FakeState()

    engaged = await check_personal_daily_loss(
        _BUNDLE,
        account_equity_krw=Decimal("0"),
        daily_realized_pnl_pct=Decimal("-0.99"),
        notifier=notifier,
        state=state,
    )

    assert engaged is False
    assert state.kill_engaged is False
    assert notifier.sent == []


async def test_exact_threshold_boundary_engages_kill():
    """경계값 — should_kill_for_daily_loss는 `<=`이므로 정확히
    -daily_loss_kill_pct(=-3%)에서도 kill이 걸린다."""
    notifier = _FakeNotifier()
    state = _FakeState()

    engaged = await check_personal_daily_loss(
        _BUNDLE,
        account_equity_krw=Decimal("1000000"),
        daily_realized_pnl_pct=Decimal("-0.03"),
        notifier=notifier,
        state=state,
    )

    assert engaged is True
    assert state.kill_engaged is True


async def test_bulk_evaluation_stays_fast_pure_function_no_io_per_call():
    """perf 회귀 감시 — kill 미발동 경로(순수 threshold 비교만 탄다)는
    1000회 평가가 수백 ms를 넘지 않아야 한다."""
    import time

    notifier = _FakeNotifier()

    start = time.perf_counter()
    for _ in range(1000):
        state = _FakeState()
        await check_personal_daily_loss(
            _BUNDLE,
            account_equity_krw=Decimal("1000000"),
            daily_realized_pnl_pct=Decimal("-0.01"),
            notifier=notifier,
            state=state,
        )
    elapsed = time.perf_counter() - start

    assert elapsed < 1.0
    assert notifier.sent == []
