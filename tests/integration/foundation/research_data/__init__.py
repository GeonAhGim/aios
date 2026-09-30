"""task-9368: RD-7/RD-9 PIT wiring evidence (INVARIANTS I-07, I-10).

Explicitly collected by the assigned task's __init__.py pytest command.
No database writes: exercise the real DSL adapter, binder and query kernel.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from time import perf_counter
from uuid import UUID

import pytest

from src.core.script.runtime.interpreter_types import CallSite
from src.core.script.runtime.series import Series
from src.foundation.market_data.domain.candle_columns import CandleColumns
from src.foundation.research_data.adapters import dsl_query
from src.foundation.research_data.application.query import search
from src.foundation.research_data.contracts.v1 import ResearchItem
from src.foundation.research_data.domain.as_of_binding import AsOfBindingError, bind_as_of

_BASE = datetime(2026, 1, 1, tzinfo=timezone.utc)
_INSTRUMENT = "test-instrument"


def _columns(n: int) -> CandleColumns:
    one = [Decimal("1")] * n
    return CandleColumns(
        ts=[_BASE + timedelta(minutes=i) for i in range(n)],
        open=one,
        high=one,
        low=one,
        close=one,
        volume=one,
        quote_volume=[None] * n,
    )


def _items() -> tuple[ResearchItem, ...]:
    return tuple(
        ResearchItem(
            item_id=UUID(int=i + 1),
            source_id="test-source",
            kind="filing",
            published_at=_BASE,
            known_at=_BASE + timedelta(minutes=i),
            instruments=(_INSTRUMENT,),
            title="PIT evidence",
            body_ref=None,
            url="https://example.test/filing",
            language="ko",
            hash=str(i),
            revision_of=None,
        )
        for i in range(3)
    )


def _counts(n: int = 3) -> list[float]:
    builtin = dsl_query.research_builtins(_items(), _columns(n), instrument=_INSTRUMENT)[
        ("research", "filing_count")
    ]
    result = builtin((), CallSite("research", "filing_count", "series<float>", n))
    assert isinstance(result, Series)
    return [result.at(i) for i in range(n)]


def test_negative_rejects_script_supplied_query_argument() -> None:
    builtin = dsl_query.research_builtins(_items(), _columns(3), instrument=_INSTRUMENT)[
        ("research", "filing_count")
    ]
    with pytest.raises(dsl_query.ResearchDslQueryError, match="takes no arguments"):
        builtin((1.0,), CallSite("research", "filing_count", "series<float>", 3))


def test_negative_rejects_bar_count_mismatch() -> None:
    builtin = dsl_query.research_builtins(_items(), _columns(3), instrument=_INSTRUMENT)[
        ("research", "filing_count")
    ]
    with pytest.raises(dsl_query.ResearchDslQueryError, match="differs from bar_count"):
        builtin((), CallSite("research", "filing_count", "series<float>", 4))


def test_negative_rejects_explicit_future_reference() -> None:
    with pytest.raises(AsOfBindingError, match="future reference rejected"):
        bind_as_of(_BASE, _BASE + timedelta(microseconds=1))


def test_negative_rejects_naive_query_timestamp() -> None:
    with pytest.raises(ValueError, match="as_of must be tz-aware"):
        search(_items(), as_of=_BASE.replace(tzinfo=None))


def test_failure_injection_binding_error_propagates_without_partial_result(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original = dsl_query.bind_as_of
    calls = []

    def fail_second_bar(bar_ts: datetime) -> datetime:
        calls.append(bar_ts)
        if len(calls) == 2:
            raise RuntimeError("injected binding failure")
        return original(bar_ts)

    with monkeypatch.context() as patch:
        patch.setattr(dsl_query, "bind_as_of", fail_second_bar)
        with pytest.raises(RuntimeError, match="injected binding failure"):
            _counts()
    assert calls == [_BASE, _BASE + timedelta(minutes=1)]
    assert _counts() == [1.0, 2.0, 3.0]


def test_adversarial_replay_verify_matches_application_query() -> None:
    """I-10: the real adapter and query agree despite backdated publication."""
    expected = [1.0, 2.0, 3.0]
    for _ in range(3):
        assert _counts() == expected
        for i, count in enumerate(expected):
            visible = search(_items(), as_of=_BASE + timedelta(minutes=i))
            assert len(visible) == count
            assert tuple(item.item_id for item in visible) == tuple(
                UUID(int=j + 1) for j in range(i + 1)
            )


def test_red_gate_reproduction_detects_future_clock_mutation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """I-07/I-10: advancing the binder's clock must trip the leakage oracle."""
    with monkeypatch.context() as patch:
        patch.setattr(dsl_query, "bind_as_of", lambda bar_ts: bar_ts + timedelta(days=1))
        leaked = _counts()
        assert leaked == [3.0, 3.0, 3.0]
        with pytest.raises(AssertionError):
            assert leaked == [1.0, 2.0, 3.0]
    assert _counts() == [1.0, 2.0, 3.0]


@pytest.mark.perf
def test_dsl_5000_bar_query_p95_latency_budget() -> None:
    """ADR-2026-09-09-C Decision 1: 5k-bar query p95 below 200ms."""
    columns = _columns(5000)
    builtin = dsl_query.research_builtins(_items(), columns, instrument=_INSTRUMENT)[
        ("research", "filing_count")
    ]
    site = CallSite("research", "filing_count", "series<float>", 5000)
    builtin((), site)
    samples = []
    for _ in range(20):
        start = perf_counter()
        result = builtin((), site)
        samples.append(perf_counter() - start)
        assert isinstance(result, Series)
        assert result.at(0) == 1.0
        assert result.at(4999) == 3.0
    assert sorted(samples)[18] < 0.2
