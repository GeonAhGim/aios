"""RD-20 — `domain/corporate_action/point_in_time.py` 순수 규칙 테스트.

Spec: docs/specs/L4_research_data_and_market_ecosystem_v1.0.md §9 RD-20.
DoD: 정정 공시 적용 후에도 정정 전 시점 질의가 정정 전 값을 돌려준다
(known_at/effective_at 분리, UPDATE 금지·새 행이 전제).
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

import pytest
from pydantic import ValidationError

from src.foundation.market_data.contracts.v1 import CorporateAction
from src.foundation.market_data.domain.corporate_action import (
    point_in_time as point_in_time_module,
)
from src.foundation.market_data.domain.corporate_action.point_in_time import resolve_as_of
from tests.conftest import PerfBudget
from tests.support.perf_inject import virtual_delayed_sync

_EX_DATE = date(2026, 4, 1)


def _action(*, instrument_id, ratio: str, known_at: datetime, source_ref: str) -> CorporateAction:
    return CorporateAction(
        action_type="SPLIT",
        instrument_id=instrument_id,
        ex_date=_EX_DATE,
        ratio=Decimal(ratio),
        source_ref=source_ref,
        known_at=known_at,
    )


def test_correction_is_a_new_row_and_pre_correction_query_returns_pre_correction_value() -> None:
    instrument_id = uuid4()
    original = _action(
        instrument_id=instrument_id,
        ratio="10",
        known_at=datetime(2026, 3, 2, 9, 0, tzinfo=UTC),
        source_ref="rcept-original",
    )
    correction = _action(
        instrument_id=instrument_id,
        ratio="5",  # 정정 공시: 실제로는 5:1 분할이었음
        known_at=datetime(2026, 3, 10, 9, 0, tzinfo=UTC),
        source_ref="rcept-correction",
    )
    history = [original, correction]  # 둘 다 저장소에 남아 있다 — UPDATE가 아니다

    before_correction = resolve_as_of(history, datetime(2026, 3, 5, tzinfo=UTC))
    after_correction = resolve_as_of(history, datetime(2026, 3, 15, tzinfo=UTC))

    assert [a.ratio for a in before_correction] == [Decimal("10")]
    assert [a.source_ref for a in before_correction] == ["rcept-original"]
    assert [a.ratio for a in after_correction] == [Decimal("5")]
    assert [a.source_ref for a in after_correction] == ["rcept-correction"]


def test_as_of_before_known_at_returns_nothing() -> None:
    instrument_id = uuid4()
    action = _action(
        instrument_id=instrument_id,
        ratio="10",
        known_at=datetime(2026, 3, 2, 9, 0, tzinfo=UTC),
        source_ref="rcept-original",
    )

    result = resolve_as_of([action], datetime(2026, 2, 1, tzinfo=UTC))

    assert result == []


def test_action_without_known_at_is_excluded() -> None:
    instrument_id = uuid4()
    legacy_action = CorporateAction(
        action_type="SPLIT",
        instrument_id=instrument_id,
        ex_date=_EX_DATE,
        ratio=Decimal("2"),
        source_ref="legacy-la12-record",
    )  # known_at 없음 — 기존 LA-12 경로에서 온 레코드

    result = resolve_as_of([legacy_action], datetime(2026, 12, 1, tzinfo=UTC))

    assert result == []


def test_naive_as_of_raises() -> None:
    action = _action(
        instrument_id=uuid4(),
        ratio="10",
        known_at=datetime(2026, 3, 2, 9, 0, tzinfo=UTC),
        source_ref="rcept-original",
    )

    with pytest.raises(ValueError):
        resolve_as_of([action], datetime(2026, 3, 5))


# ---- DEEPEN(task-4172) — negative / failure-injection / perf ----------------


def test_known_at_one_microsecond_after_as_of_is_strictly_excluded() -> None:
    """Negative: RD-20's invariant is `known_at <= as_of`, not "close enough" —
    an action known exactly one microsecond after `as_of` must be excluded.
    Proves the boundary comparison is a strict `>` rejection and doesn't
    round or truncate to second/date granularity (an off-by-one here would
    leak a not-yet-known correction into a backtest)."""
    instrument_id = uuid4()
    as_of = datetime(2026, 3, 5, 12, 0, 0, tzinfo=UTC)
    action = _action(
        instrument_id=instrument_id,
        ratio="10",
        known_at=as_of + timedelta(microseconds=1),
        source_ref="rcept-future-by-1us",
    )

    result = resolve_as_of([action], as_of)

    assert result == []


def test_corporate_action_rejects_naive_known_at_at_construction() -> None:
    """Negative: RD-20's known_at invariant is enforced by the contract itself
    (`AwareDatetime`), not by `resolve_as_of` — a naive (tz-missing, non-None)
    `known_at` can never reach this module in the first place. Complements
    `test_action_without_known_at_is_excluded` (None is accepted and
    excluded) by proving a naive value is rejected outright instead of being
    silently treated as UTC."""
    with pytest.raises(ValidationError):
        CorporateAction(
            action_type="SPLIT",
            instrument_id=uuid4(),
            ex_date=_EX_DATE,
            ratio=Decimal("10"),
            source_ref="rcept-naive",
            known_at=datetime(2026, 3, 2, 9, 0),  # tzinfo 없음
        )


def test_resolve_as_of_propagates_unexpected_sort_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    """Failure injection: if the final `sorted(...)` step raises unexpectedly
    (e.g. a future edit makes the sort key computation throw), `resolve_as_of`
    must propagate the exception rather than swallow it and return a
    partial/empty result — fail-closed per CLAUDE.md §3 default posture."""

    def _raising_sorted(*args: object, **kwargs: object) -> list[object]:
        raise RuntimeError("sort kernel failure (injected)")

    monkeypatch.setattr(point_in_time_module, "sorted", _raising_sorted, raising=False)

    action = _action(
        instrument_id=uuid4(),
        ratio="10",
        known_at=datetime(2026, 3, 2, 9, 0, tzinfo=UTC),
        source_ref="rcept-original",
    )

    with pytest.raises(RuntimeError, match="injected"):
        resolve_as_of([action], datetime(2026, 3, 5, tzinfo=UTC))


@pytest.mark.perf
def test_resolve_as_of_meets_latency_budget_with_many_instruments_and_corrections(
    perf_budget: PerfBudget,
) -> None:
    """수천 개 종목 x 정정 이력에 대한 단건 `as_of` 판정이 절대시간 예산
    내여야 한다 — 그룹핑(dict 축약)이 선형 스캔에서 이차로 퇴화하지
    않았는지 확인한다(ADR-2026-09-09-C Decision 1)."""
    n_instruments = 2_000
    budget_ms = 2_000.0  # 실측 로컬 <0.4s(400ms)
    base_known_at = datetime(2026, 1, 1, tzinfo=UTC)
    as_of = base_known_at + timedelta(days=2)

    actions: list[CorporateAction] = []
    for i in range(n_instruments):
        instrument_id = uuid4()
        actions.append(
            _action(
                instrument_id=instrument_id,
                ratio="10",
                known_at=base_known_at,
                source_ref=f"rcept-{i}-original",
            )
        )
        actions.append(
            _action(
                instrument_id=instrument_id,
                ratio="5",
                known_at=base_known_at + timedelta(days=1),
                source_ref=f"rcept-{i}-correction",
            )
        )
        actions.append(  # as_of보다 미래 — 안 보여야 한다
            _action(
                instrument_id=instrument_id,
                ratio="1",
                known_at=base_known_at + timedelta(days=5),
                source_ref=f"rcept-{i}-future-correction",
            )
        )

    result: list[CorporateAction] = []

    def _resolve() -> None:
        nonlocal result
        result = resolve_as_of(actions, as_of)

    # 단일 호출이 이미 ~400ms로 15.6ms process_time 틱의 수십 배라 batch=1로도
    # 양자화 오차가 무시 가능하다 — batch는 빠른 호출의 틱 희석용이지 여기선
    # 불필요(note에 기록).
    sample = perf_budget.sample(_resolve, batch=1)

    print(
        f"[RD-20 resolve_as_of] {len(actions)}건({n_instruments} 종목) 판정 "
        f"{perf_budget.describe(sample, budget_ms=budget_ms)}"
    )
    assert len(result) == n_instruments
    assert all(a.ratio == Decimal("5") for a in result)
    assert sample.cpu_ms < budget_ms, (
        f"{len(actions)}건 판정이 예산({budget_ms:.1f}ms)을 넘었습니다({sample.cpu_ms:.4f}ms)."
    )


def test_single_call_cpu_measurement_does_not_converge_to_zero(
    perf_budget: PerfBudget,
) -> None:
    """CPU measurement quality: a single fast call on `resolve_as_of` must
    record measurable CPU time (not 0 ms) when the input is large enough to
    exceed Windows `time.process_time()` quantization (~15.6 ms).

    This proves the perf budget assertion in
    `test_resolve_as_of_meets_latency_budget_with_many_instruments_and_corrections`
    is not a false negative caused by quantisation to zero.
    """
    # 5 000 instruments → ~10 000 actions; resolve_as_of의 dict/hash 경로는
    # O(n)이지만 상수가 매우 작아 15.6 ms Windows quantisation tick을
    # 넘어서 실제 CPU 시간을 측정할 수 있다. (기존 perf 테스트의
    # 2 000 종목이 ~400ms이므로 5 000 종목은 ~1초대 — quantisation을
    # 완전히 덮는다.)
    # task-19907: 5 000 instruments measured only ~9 ms wall under CI load, i.e. less than one
    # 15.6 ms tick, so a single call read cpu_ms=0.0 (~40% per sample). 50 000 instruments
    # (~90 ms+) spans several ticks, so a zero reading is impossible rather than unlikely.
    n_instruments = 50_000
    base_known_at = datetime(2026, 1, 1, tzinfo=UTC)
    as_of = base_known_at + timedelta(days=2)

    actions: list[CorporateAction] = []
    for i in range(n_instruments):
        instrument_id = uuid4()
        actions.append(
            _action(
                instrument_id=instrument_id,
                ratio="10",
                known_at=base_known_at,
                source_ref=f"rcept-{i}-original",
            )
        )
        actions.append(
            _action(
                instrument_id=instrument_id,
                ratio="5",
                known_at=base_known_at + timedelta(days=1),
                source_ref=f"rcept-{i}-correction",
            )
        )

    # best_of: 여러 번 중 최소(cpu_ms)를 읽어 Windows 양자화 tick 1회에
    # 흔들리지 않게 한다(예산·단언 숫자는 올리지 않음).
    sample = perf_budget.best_of(lambda: resolve_as_of(actions, as_of), n=5, batch=1)

    # single-call CPU 측정값이 0이 아님을 확인 — quantisation artefact 방지
    assert sample.cpu_ms > 0, (
        f"단일 호출 CPU 측정이 0 ms로 수렴했습니다. "
        f"Windows quantisation tick({sample.cpu_ms:.4f}ms)이 예산 단언을 "
        f"false-negative로 만들 수 있습니다."
    )
    # 그리고 같은 입력이 기존 예산도 통과해야 한다.
    assert sample.cpu_ms < 10_000.0, (
        f"50 000 종목 단일 호출이 10 s를 넘었습니다({sample.cpu_ms:.4f}ms)."
    )


@pytest.mark.perf
def test_resolve_as_of_perf_assertion_becomes_red_under_delayed_load(
    perf_budget: PerfBudget,
) -> None:
    """Failure-injection gate-red reproduction: inject a 0.5 s CPU-heavy
    delay via `delayed_sync` and prove the budget assertion turns red —
    confirming the assertion is live and not a false-green.

    Budget is set to 100 ms (well within the ~15.6 ms quantisation tick
    so any real CPU work is measurable). A 0.5 s injected delay must
    exceed it, turning the green assertion red.
    """
    n_instruments = 10
    budget_ms = 100.0
    base_known_at = datetime(2026, 1, 1, tzinfo=UTC)
    as_of = base_known_at + timedelta(days=2)

    actions: list[CorporateAction] = []
    for i in range(n_instruments):
        instrument_id = uuid4()
        actions.append(
            _action(
                instrument_id=instrument_id,
                ratio="10",
                known_at=base_known_at,
                source_ref=f"rcept-{i}-original",
            )
        )
        actions.append(
            _action(
                instrument_id=instrument_id,
                ratio="5",
                known_at=base_known_at + timedelta(days=1),
                source_ref=f"rcept-{i}-correction",
            )
        )

    # `resolve_as_of`를 0.5초 가상 지연으로 감싼 버전 — 실제 busy-wait 없이
    # PerfBudget가 virtual_totals()로 누계한 값을 측정치에 더한다.
    monkeypatch_delayed = virtual_delayed_sync(resolve_as_of, 0.5)

    result: list[CorporateAction] = []

    def _resolve() -> None:
        nonlocal result
        result = monkeypatch_delayed(actions, as_of)

    sample = perf_budget.sample(_resolve, batch=1)

    print(
        f"[RD-20 delayed] {len(actions)}건({n_instruments} 종목) 지연 부하 "
        f"{perf_budget.describe(sample, budget_ms=budget_ms)}"
    )
    # 정상 입력에서는 통과하는 예산이 지연 주입으로 인해 적색 —
    # 단언이 live함을 증명한다.
    assert sample.cpu_ms >= budget_ms, (
        f"지연 주입에도 CPU 측정이 예산({budget_ms:.1f}ms) 아래에 머물렀습니다. "
        f"({sample.cpu_ms:.4f}ms) — 지연 주입이 제대로 작용하지 않았거나 "
        f"예산이 과도하게 느슨합니다."
    )
