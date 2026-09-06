"""OCO 형제 조정 단위테스트 — EM-19. DB 없음.

DoD: OCO 형제 취소가 원자적(1000회 적대 실행에서 이중 체결 0)."""
from __future__ import annotations

import threading

from src.services.oms.domain.order_types.oco import OcoGroup, OcoOutcome, resolve_oco


def test_resolve_oco_triggers_leg_a_only() -> None:
    result = resolve_oco(leg_a_triggered=True, leg_b_triggered=False, priority_leg="a")
    assert result.triggered_leg == "a"
    assert result.cancelled_leg == "b"


def test_resolve_oco_triggers_leg_b_only() -> None:
    result = resolve_oco(leg_a_triggered=False, leg_b_triggered=True, priority_leg="a")
    assert result.triggered_leg == "b"
    assert result.cancelled_leg == "a"


def test_resolve_oco_neither_triggered() -> None:
    result = resolve_oco(leg_a_triggered=False, leg_b_triggered=False, priority_leg="a")
    assert result.triggered_leg == "none"
    assert result.cancelled_leg == "none"


def test_resolve_oco_both_triggered_same_tick_uses_priority_leg() -> None:
    result = resolve_oco(leg_a_triggered=True, leg_b_triggered=True, priority_leg="b")
    assert result.triggered_leg == "b"
    assert result.cancelled_leg == "a"


def test_oco_group_first_leg_wins_and_sibling_is_cancelled() -> None:
    group = OcoGroup()
    assert group.try_trigger("a") is OcoOutcome.TRIGGERED
    assert group.try_trigger("b") is OcoOutcome.CANCELLED


def test_oco_group_repeated_calls_are_idempotent() -> None:
    group = OcoGroup()
    assert group.try_trigger("a") is OcoOutcome.TRIGGERED
    assert group.try_trigger("a") is OcoOutcome.TRIGGERED
    assert group.try_trigger("b") is OcoOutcome.CANCELLED
    assert group.try_trigger("b") is OcoOutcome.CANCELLED
    assert group.winner == "a"


def test_oco_group_atomic_under_1000_adversarial_concurrent_triggers() -> None:
    """양쪽 레그가 각각 500개 스레드로 동시에 `try_trigger`를 호출해도
    TRIGGERED는 정확히 한 레그에서만 나와야 한다 — 두 레그가 동시에
    TRIGGERED가 되는 이중 체결은 0이어야 한다."""
    group = OcoGroup()
    outcomes: dict[str, list[OcoOutcome]] = {"a": [], "b": []}
    lock = threading.Lock()

    def _attempt(leg: str) -> None:
        outcome = group.try_trigger(leg)  # type: ignore[arg-type]
        with lock:
            outcomes[leg].append(outcome)

    threads = [
        threading.Thread(target=_attempt, args=(leg,))
        for _ in range(500)
        for leg in ("a", "b")
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert len(outcomes["a"]) == 500
    assert len(outcomes["b"]) == 500

    a_triggered = outcomes["a"].count(OcoOutcome.TRIGGERED)
    b_triggered = outcomes["b"].count(OcoOutcome.TRIGGERED)

    # 정확히 한쪽 레그만 TRIGGERED를 받아야 한다(그 레그의 모든 500회가
    # TRIGGERED, 다른 레그의 모든 500회는 CANCELLED) — 이중 체결 0.
    assert (a_triggered, b_triggered) in [(500, 0), (0, 500)]
    assert outcomes["a"].count(OcoOutcome.CANCELLED) + a_triggered == 500
    assert outcomes["b"].count(OcoOutcome.CANCELLED) + b_triggered == 500
    assert group.winner in ("a", "b")
