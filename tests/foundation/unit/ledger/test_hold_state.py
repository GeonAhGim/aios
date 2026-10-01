"""LC-5 — hold_state 단위 테스트.

Spec: docs/specs/L4_market_data_positions_ledger_v1.0.md#§9 LC-5
("전이표 전수", negative: "만료된 hold 재사용").
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

import pytest

from src.foundation.ledger.contracts.v1 import HoldState
from src.foundation.ledger.domain import hold_state as hs
from tests.conftest import PerfBudget

_NOW = datetime(2026, 9, 3, tzinfo=timezone.utc)
_NOT_EXPIRED = _NOW + timedelta(days=1)
_ALREADY_EXPIRED = _NOW - timedelta(days=1)

_ALL_STATES: list[HoldState | None] = [
    None,
    HoldState.PENDING,
    HoldState.CAPTURED,
    HoldState.RELEASED,
    HoldState.EXPIRED,
]
_ALL_EVENTS = list(hs.HoldEvent)

_LEGAL: dict[tuple[HoldState | None, hs.HoldEvent], HoldState] = {
    (None, hs.HoldEvent.PLACE): HoldState.PENDING,
    (HoldState.PENDING, hs.HoldEvent.CAPTURE): HoldState.CAPTURED,
    (HoldState.PENDING, hs.HoldEvent.RELEASE): HoldState.RELEASED,
    (HoldState.PENDING, hs.HoldEvent.EXPIRE): HoldState.EXPIRED,
}


def _expires_at_satisfying_guard(event: hs.HoldEvent) -> datetime:
    """EXPIRE는 now > expires_at을 요구하고 CAPTURE/그 외는 now <= expires_at을
    요구한다 — 전이 합법성만 보고 싶은 테스트가 가드 실패로 흔들리지 않게 한다."""
    return _ALREADY_EXPIRED if event is hs.HoldEvent.EXPIRE else _NOT_EXPIRED


def test_legal_transitions_produce_expected_state() -> None:
    for (frm, event), to in _LEGAL.items():
        got = hs.transition(frm, event, now=_NOW, expires_at=_expires_at_satisfying_guard(event))
        assert got is to, f"{frm} --{event}--> expected {to}, got {got}"


@pytest.mark.parametrize("frm", _ALL_STATES)
@pytest.mark.parametrize("event", _ALL_EVENTS)
def test_transition_table_is_exhaustive(frm: HoldState | None, event: hs.HoldEvent) -> None:
    """허용된 4개 조합 외에는 전부 거부된다(§4.5 전이표 전수)."""
    expires_at = _expires_at_satisfying_guard(event)
    if (frm, event) in _LEGAL:
        hs.transition(frm, event, now=_NOW, expires_at=expires_at)
        return
    with pytest.raises(hs.IllegalHoldTransitionError):
        hs.transition(frm, event, now=_NOW, expires_at=expires_at)


# --- negative ---


def test_capture_after_expiry_rejected() -> None:
    """만료된 hold 재사용: capture 시각이 expires_at을 지났으면 거부."""
    with pytest.raises(hs.HoldExpiredError):
        hs.transition(
            HoldState.PENDING, hs.HoldEvent.CAPTURE, now=_NOW, expires_at=_ALREADY_EXPIRED
        )


def test_expire_before_expiry_time_rejected() -> None:
    with pytest.raises(hs.HoldNotYetExpiredError):
        hs.transition(HoldState.PENDING, hs.HoldEvent.EXPIRE, now=_NOW, expires_at=_NOT_EXPIRED)


def test_capture_exactly_at_expiry_boundary_is_allowed() -> None:
    """now == expires_at은 아직 만료 전(guard는 now > expires_at일 때만 거부)."""
    got = hs.transition(HoldState.PENDING, hs.HoldEvent.CAPTURE, now=_NOW, expires_at=_NOW)
    assert got is HoldState.CAPTURED


def test_terminal_states_reject_every_event() -> None:
    for frm in (HoldState.CAPTURED, HoldState.RELEASED, HoldState.EXPIRED):
        for event in _ALL_EVENTS:
            with pytest.raises(hs.IllegalHoldTransitionError):
                hs.transition(frm, event, now=_NOW, expires_at=_NOT_EXPIRED)


# ── DEEPEN D3: 성능/동시성/게이트 적색 재현 ──────────────────────────
#
# 실패 주입(의존성 예외) 항목: N/A(hold_state.transition은 순수 함수로
# DB/네트워크/외부 모듈 호출이 전혀 없다 — docstring에 명시된 설계대로
# I/O·시계·난수 호출이 없으므로 side_effect=...Error로 흔들 수 있는
# 의존성이 존재하지 않는다; 동일 사유로 이미 존재하는
# test_funding_fees.py 류의 "monkeypatch 대상"도 이 모듈에는 없다).


@pytest.mark.perf
def test_transition_batch_10000_calls_within_latency_budget(perf_budget: PerfBudget) -> None:
    """수치 성능 단언: 10,000회 전이 판정이 50ms 예산 안에 끝나야 한다
    (순수 dict 조회 + 비교 연산 — O(1) per call). task-7434 공용 perf_budget
    (process_time 기반)을 쓴다 — raw time.perf_counter() 금지."""
    n = 10_000
    budget_ms = 50.0

    def _run_once() -> None:
        for _ in range(n):
            hs.transition(
                HoldState.PENDING, hs.HoldEvent.CAPTURE, now=_NOW, expires_at=_NOT_EXPIRED
            )

    perf_budget.assert_within(_run_once, budget_ms=budget_ms, label=f"{n} transition calls")


def test_transition_concurrent_calls_do_not_cross_contaminate() -> None:
    """D3 동시성 증빙: `transition`은 순수 함수(모듈 전역 가변 상태 없음)
    이므로 여러 스레드가 서로 다른 (from, event, now/expires_at) 조합으로
    동시에 호출해도 각자의 결과가 다른 호출의 인자로 오염되면 안 된다.
    향후 누군가 캐시나 공유 가변 상태를 도입하면 이 테스트가 깨진다."""
    combos: list[tuple[HoldState | None, hs.HoldEvent, datetime]] = []
    for frm, event in _LEGAL:
        combos.append((frm, event, _expires_at_satisfying_guard(event)))
    for frm in (HoldState.CAPTURED, HoldState.RELEASED, HoldState.EXPIRED):
        combos.append((frm, hs.HoldEvent.CAPTURE, _NOT_EXPIRED))

    def _run(i: int) -> tuple[int, HoldState | None, hs.HoldEvent, HoldState | None]:
        frm, event, expires_at = combos[i % len(combos)]
        try:
            result = hs.transition(frm, event, now=_NOW, expires_at=expires_at)
        except hs.IllegalHoldTransitionError:
            result = None
        return i, frm, event, result

    with ThreadPoolExecutor(max_workers=16) as pool:
        results = list(pool.map(_run, range(400)))

    for i, frm, event, result in results:
        expected = _LEGAL.get((frm, event))
        assert result is expected, (i, frm, event)


def test_gate_red_repro_check_perf_marker_guard_flags_unmarked_perf_counter_assert(
    tmp_path: object,
) -> None:
    """게이트 적색 재현 -- `scripts/check_perf_marker_guard.py`(CI에서
    `python scripts/check_perf_marker_guard.py`로 실행되는 정적 검사,
    main() 종료코드 0=통과)가 이 파일의
    `test_transition_batch_10000_calls_within_latency_budget`처럼
    `time.perf_counter()` 차이를 `assert`로 직접 검증하는 함수에
    `@pytest.mark.perf`가 없으면 실제로 적색 처리하는지, main() 자체의
    종료코드로 증명한다. 가짜 tests 루트를 만들어 `--tests-root`만
    바꿔치기하므로 실제 저장소 파일은 건드리지 않는다."""
    import scripts.check_perf_marker_guard as guard

    marked = tmp_path / "test_marked_ok.py"
    marked.write_text(
        "import time\n"
        "import pytest\n"
        "\n"
        "@pytest.mark.perf\n"
        "def test_something_fast():\n"
        "    started = time.perf_counter()\n"
        "    elapsed = time.perf_counter() - started\n"
        "    assert elapsed < 5.0\n",
        encoding="utf-8",
    )

    assert guard.main(["--tests-root", str(tmp_path)]) == 0  # 대조군 -- marker 있으면 통과

    unmarked = tmp_path / "test_unmarked_regression.py"
    unmarked.write_text(
        "import time\n"
        "\n"
        "def test_something_fast_but_unmarked():\n"
        "    started = time.perf_counter()\n"
        "    elapsed = time.perf_counter() - started\n"
        "    assert elapsed < 5.0\n",
        encoding="utf-8",
    )

    assert guard.main(["--tests-root", str(tmp_path)]) == 1  # marker 누락 시 적색으로 뒤집힘
