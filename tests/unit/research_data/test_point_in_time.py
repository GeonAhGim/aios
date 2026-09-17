"""RD-20 — `domain/corporate_action/point_in_time.py` 순수 규칙 테스트.

Spec: docs/specs/L4_research_data_and_market_ecosystem_v1.0.md §9 RD-20.
DoD: 정정 공시 적용 후에도 정정 전 시점 질의가 정정 전 값을 돌려준다
(known_at/effective_at 분리, UPDATE 금지·새 행이 전제).
"""

from __future__ import annotations

import time
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from uuid import uuid4

import pytest
from pydantic import ValidationError

from src.foundation.market_data.contracts.v1 import CorporateAction
from src.foundation.market_data.domain.corporate_action import (
    point_in_time as point_in_time_module,
)
from src.foundation.market_data.domain.corporate_action.point_in_time import resolve_as_of

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
        known_at=datetime(2026, 3, 2, 9, 0, tzinfo=timezone.utc),
        source_ref="rcept-original",
    )
    correction = _action(
        instrument_id=instrument_id,
        ratio="5",  # 정정 공시: 실제로는 5:1 분할이었음
        known_at=datetime(2026, 3, 10, 9, 0, tzinfo=timezone.utc),
        source_ref="rcept-correction",
    )
    history = [original, correction]  # 둘 다 저장소에 남아 있다 — UPDATE가 아니다

    before_correction = resolve_as_of(history, datetime(2026, 3, 5, tzinfo=timezone.utc))
    after_correction = resolve_as_of(history, datetime(2026, 3, 15, tzinfo=timezone.utc))

    assert [a.ratio for a in before_correction] == [Decimal("10")]
    assert [a.source_ref for a in before_correction] == ["rcept-original"]
    assert [a.ratio for a in after_correction] == [Decimal("5")]
    assert [a.source_ref for a in after_correction] == ["rcept-correction"]


def test_as_of_before_known_at_returns_nothing() -> None:
    instrument_id = uuid4()
    action = _action(
        instrument_id=instrument_id,
        ratio="10",
        known_at=datetime(2026, 3, 2, 9, 0, tzinfo=timezone.utc),
        source_ref="rcept-original",
    )

    result = resolve_as_of([action], datetime(2026, 2, 1, tzinfo=timezone.utc))

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

    result = resolve_as_of([legacy_action], datetime(2026, 12, 1, tzinfo=timezone.utc))

    assert result == []


def test_naive_as_of_raises() -> None:
    action = _action(
        instrument_id=uuid4(),
        ratio="10",
        known_at=datetime(2026, 3, 2, 9, 0, tzinfo=timezone.utc),
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
    as_of = datetime(2026, 3, 5, 12, 0, 0, tzinfo=timezone.utc)
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
        known_at=datetime(2026, 3, 2, 9, 0, tzinfo=timezone.utc),
        source_ref="rcept-original",
    )

    with pytest.raises(RuntimeError, match="injected"):
        resolve_as_of([action], datetime(2026, 3, 5, tzinfo=timezone.utc))


@pytest.mark.perf
def test_resolve_as_of_meets_latency_budget_with_many_instruments_and_corrections() -> None:
    """수천 개 종목 x 정정 이력에 대한 단건 `as_of` 판정이 절대시간 예산
    내여야 한다 — 그룹핑(dict 축약)이 선형 스캔에서 이차로 퇴화하지
    않았는지 확인한다(ADR-2026-09-09-C Decision 1)."""
    n_instruments = 2_000
    budget_sec = 2.0  # 실측 로컬 <0.4s
    base_known_at = datetime(2026, 1, 1, tzinfo=timezone.utc)
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

    start = time.perf_counter()
    result = resolve_as_of(actions, as_of)
    elapsed = time.perf_counter() - start

    print(
        f"[RD-20 resolve_as_of] {len(actions)}건({n_instruments} 종목) 판정 "
        f"{elapsed:.4f}s (budget<{budget_sec}s)"
    )
    assert len(result) == n_instruments
    assert all(a.ratio == Decimal("5") for a in result)
    assert elapsed < budget_sec, (
        f"{len(actions)}건 판정이 예산({budget_sec}s)을 넘었습니다({elapsed:.4f}s)."
    )
