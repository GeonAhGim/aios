"""task-8415: RD-2/RD-7 fail-closed and PIT wiring regression tests.

Explicitly collected by the task command targeting this package initializer.
RD-A1 and I-07/I-10: reject invalid coordinates and propagate kernel failure.
"""

from datetime import datetime, timedelta, timezone
from uuid import UUID

import pytest
from pydantic import ValidationError

from src.foundation.research_data.application import query
from src.foundation.research_data.contracts.v1 import ResearchItem
from src.foundation.research_data.domain.known_at import (
    PointInTimeViolationError,
    assert_point_in_time,
)

_BASE = datetime(2026, 9, 1, tzinfo=timezone.utc)


def _item() -> ResearchItem:
    return ResearchItem(
        item_id=UUID(int=1),
        source_id="test",
        kind="news",
        published_at=_BASE,
        known_at=_BASE,
        instruments=("KR:A005930",),
        title="PIT regression",
        body_ref=None,
        url="https://example.com/item",
        language="ko",
        hash="fixture",
        revision_of=None,
    )


def test_negative_missing_known_at_is_rejected() -> None:
    payload = _item().model_dump()
    del payload["known_at"]
    with pytest.raises(ValidationError) as caught:
        ResearchItem.model_validate(payload)
    assert [(e["loc"], e["type"]) for e in caught.value.errors()] == [(("known_at",), "missing")]


def test_negative_naive_known_at_is_rejected() -> None:
    payload = _item().model_dump()
    payload["known_at"] = _BASE.replace(tzinfo=None)
    with pytest.raises(ValidationError) as caught:
        ResearchItem.model_validate(payload)
    assert [(e["loc"], e["type"]) for e in caught.value.errors()] == [
        (("known_at",), "timezone_aware")
    ]


def test_negative_naive_query_coordinate_is_rejected() -> None:
    with pytest.raises(ValueError, match="as_of must be tz-aware"):
        query.search([_item()], as_of=_BASE.replace(tzinfo=None))


def test_negative_future_item_is_rejected_at_microsecond_boundary() -> None:
    item = _item()
    probe = _BASE - timedelta(microseconds=1)
    with pytest.raises(PointInTimeViolationError) as caught:
        assert_point_in_time(item, probe)
    assert caught.value.item is item
    assert caught.value.as_of_time == probe
    assert query.search([item], as_of=probe) == ()
    assert query.search([item], as_of=_BASE) == (item,)


def test_failure_injection_kernel_exception_is_not_empty_success(monkeypatch) -> None:
    failure = RuntimeError("injected PIT kernel failure")
    calls = []

    def broken_kernel(records, *, valid_time, tx_time):
        calls.append((records, valid_time, tx_time))
        raise failure

    monkeypatch.setattr(query, "_bitemporal_as_of", broken_kernel)
    item = _item()
    with pytest.raises(RuntimeError, match="injected PIT kernel failure") as caught:
        query.search([item], as_of=_BASE)
    assert caught.value is failure
    assert len(calls) == 1
    assert calls[0][0][0].value is item
    assert calls[0][1:] == (_BASE, _BASE)


def test_red_gate_reproduction_detects_bypassed_pit_filter(monkeypatch) -> None:
    def bypass(records, **kwargs):
        return records

    monkeypatch.setattr(query, "_bitemporal_as_of", bypass)
    with pytest.raises(AssertionError):
        test_negative_future_item_is_rejected_at_microsecond_boundary()


@pytest.mark.perf
def test_query_5000_items_p95_budget(perf_budget) -> None:
    """ADR-2026-09-09-C 5k query budget: p95 CPU below 200ms."""
    template = _item()
    items = [template.model_copy(update={"item_id": UUID(int=i + 1)}) for i in range(5000)]
    samples = perf_budget.samples(lambda: query.search(items, as_of=_BASE), n=20)
    for sample in samples:
        assert sample.result == tuple(items)
    p95 = sorted(sample.cpu_ms for sample in samples)[18]
    assert p95 < 200.0, f"5k research query p95={p95:.3f}ms, budget=200ms"
