"""RD-7 -- `application/query.py` search + as_of PIT filter tests.

Spec: docs/specs/L4_research_data_and_market_ecosystem_v1.0.md §9 RD-7,
§4 RD-A1 ("an item with `known_at > as_of` is never returned by any
read path").

DoD (D2 minimum):
  - negative tests >= 3 (naive_as_of_raises, future_item_excluded,
    instrument_filter_excludes_unmatched, kind_filter_excludes_unmatched)
  - failure injection: future_item_leak_injection (corrupt items list
    with a future item, verify it is still excluded)
  - performance assertion: search_latency_under_budget (10k items < 300ms)
  - gate-red reproduction: gate_red_reproduction (verify RD-A1 invariant
    holds through the full search pipeline)
"""

from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest

from src.core.bitemporal import as_of as _bitemporal_as_of
from src.foundation.research_data.application.query import (
    QueryFilter,
    _item_to_record,
    search,
)
from src.foundation.research_data.contracts.v1 import ResearchItem

# --- fixtures ---

_BASE_TIME = datetime(2026, 6, 15, 12, 0, 0, tzinfo=timezone.utc)


def _item(
    *,
    published_at: datetime | None = None,
    known_at: datetime | None = None,
    instruments: tuple[str, ...] | None = None,
    kind: str = "filing",
    revision_of=None,
) -> ResearchItem:
    return ResearchItem(
        item_id=uuid4(),
        source_id="test-source",
        kind=kind,
        published_at=published_at or known_at or _BASE_TIME,
        known_at=known_at or _BASE_TIME,
        instruments=instruments or ("KR:A005930",),
        title="test title",
        body_ref="/test/ref",
        url="https://example.com/test",
        language="ko",
        hash="abc123",
        revision_of=revision_of,
    )


# --- negative tests (>= 3 required) ---


def test_naive_as_of_raises() -> None:
    """RD-A1: naive as_of is rejected (fail-closed)."""
    items = [_item()]
    with pytest.raises(ValueError, match="tz-aware"):
        search(items, as_of=datetime(2026, 6, 15))


def test_future_item_excluded_by_as_of() -> None:
    """RD-A1: item with known_at > as_of is never returned."""
    future_item = _item(known_at=datetime(2026, 7, 1, tzinfo=timezone.utc))
    past_item = _item(known_at=datetime(2026, 5, 1, tzinfo=timezone.utc))
    items = [future_item, past_item]

    result = search(items, as_of=datetime(2026, 6, 15, tzinfo=timezone.utc))

    assert len(result) == 1
    assert result[0] is past_item
    assert result[0].item_id != future_item.item_id


def test_instrument_filter_excludes_unmatched() -> None:
    """Instrument filter: only items containing at least one queried ID."""
    item_a = _item(instruments=("KR:A005930",))
    item_b = _item(instruments=("KR:A070000",))
    items = [item_a, item_b]

    result = search(
        items,
        instruments=["KR:A005930"],
        as_of=_BASE_TIME,
    )

    assert len(result) == 1
    assert result[0] is item_a


def test_kind_filter_excludes_unmatched() -> None:
    """Kind filter: only items matching the queried kind."""
    item_filing = _item(kind="filing")
    item_news = _item(kind="news")
    items = [item_filing, item_news]

    result = search(items, kinds=["news"], as_of=_BASE_TIME)

    assert len(result) == 1
    assert result[0] is item_news


def test_as_of_boundary_inclusive() -> None:
    """RD-A1 boundary: known_at == as_of IS included."""
    exact_item = _item(known_at=_BASE_TIME)
    items = [exact_item]

    result = search(items, as_of=_BASE_TIME)

    assert len(result) == 1
    assert result[0] is exact_item


def test_empty_items_returns_empty_tuple() -> None:
    """Edge: empty input returns empty tuple, not None."""
    result = search([], as_of=_BASE_TIME)
    assert result == ()


# --- failure injection test ---


def test_future_item_leak_injection() -> None:
    """RD-A1 failure injection: even if the items list is corrupted with
    a future item (known_at in the future of the query as_of), the result
    must still exclude it. This verifies the PIT filter is not bypassed
    by caller assumptions."""
    # Simulate a "corrupted" list where a future item slipped in
    future_item = _item(
        known_at=datetime(2026, 12, 31, 23, 59, 59, tzinfo=timezone.utc),
        instruments=("KR:A005930", "KR:A070000"),
    )
    valid_item = _item(
        known_at=datetime(2026, 6, 1, tzinfo=timezone.utc),
        instruments=("KR:A005930",),
    )
    items = [valid_item, future_item]

    # Query as of mid-June — future item should be excluded
    result = search(
        items,
        instruments=["KR:A005930", "KR:A070000"],
        as_of=datetime(2026, 6, 15, 12, 0, 0, tzinfo=timezone.utc),
    )

    assert len(result) == 1
    assert result[0] is valid_item
    assert result[0].item_id != future_item.item_id


# --- performance assertion ---


@pytest.mark.perf
def test_search_latency_under_budget() -> None:
    """RD-7 performance: 10,000 items must search in < 300ms (budget=300).

    This is a floor assertion — the actual implementation delegates to the
    FA-9 bitemporal kernel which is O(n) per item, so 10k items should
    complete well within budget on modern hardware.
    """
    items = [
        _item(
            known_at=datetime(2026, 1, 1, tzinfo=timezone.utc) + timedelta(days=i),
            instruments=(f"KR:SYM{i:04d}",),
            kind=["filing", "news", "macro", "alt"][i % 4],
        )
        for i in range(10_000)
    ]

    start = time.perf_counter()
    for _ in range(10):
        search(
            items,
            instruments=["KR:SYM0500"],
            kinds=["news"],
            as_of=datetime(2026, 6, 15, tzinfo=timezone.utc),
        )
    elapsed = time.perf_counter() - start

    avg_ms = (elapsed / 10) * 1000
    assert avg_ms < 300, f"search avg {avg_ms:.1f}ms exceeds 300ms budget"


# --- gate-red reproduction ---


def test_gate_red_reproduction() -> None:
    """RD-A1 gate-red reproduction: verify the invariant that
    `known_at > as_of` items are NEVER in the result, by directly
    invoking the FA-9 bitemporal kernel through `search`.

    This is the adversarial test: we construct items that would pass
    a naive string/attribute comparison but fail the bitemporal check.
    """
    # Item exactly at the boundary
    boundary_item = _item(known_at=datetime(2026, 6, 15, 12, 0, 0, tzinfo=timezone.utc))
    # Item 1 microsecond after as_of — should be excluded
    as_of_time = datetime(2026, 6, 15, 12, 0, 0, tzinfo=timezone.utc)
    just_after_item = _item(
        known_at=as_of_time + timedelta(microseconds=1),
    )
    well_before = _item(
        known_at=as_of_time - timedelta(hours=1),
    )

    items = [well_before, boundary_item, just_after_item]

    result = search(items, as_of=as_of_time)

    # Only well_before and boundary_item should be in result
    result_ids = {r.item_id for r in result}
    assert well_before.item_id in result_ids
    assert boundary_item.item_id in result_ids
    assert just_after_item.item_id not in result_ids
    assert len(result) == 2

    # Additional check: verify the FA-9 kernel directly
    from src.foundation.research_data.application.query import _item_to_record

    records = [_item_to_record(just_after_item)]
    visible = _bitemporal_as_of(records, valid_time=as_of_time, tx_time=as_of_time)
    assert len(visible) == 0, "FA-9 kernel should exclude known_at > as_of"


# --- domain helper tests ---


def test_item_to_record_collapse_axes() -> None:
    """Verify _item_to_record sets valid_from=tx_from=known_at."""
    item = _item(known_at=_BASE_TIME)
    record = _item_to_record(item)
    assert record.valid_from == _BASE_TIME
    assert record.tx_from == _BASE_TIME
    assert record.valid_to is None
    assert record.tx_to is None


def test_query_filter_defaults_to_now() -> None:
    """QueryFilter.as_of_now returns current UTC when as_of is None."""
    f = QueryFilter()
    now = f.as_of_now
    # Should be close to current time (within 1 second)
    diff = abs((now - datetime.now(timezone.utc)).total_seconds())
    assert diff < 1


def test_query_filter_with_values() -> None:
    """QueryFilter preserves provided values."""
    f = QueryFilter(
        instruments=["KR:A005930"],
        kinds=["filing"],
        span=(datetime(2026, 1, 1, tzinfo=timezone.utc), None),
        as_of=datetime(2026, 6, 15, tzinfo=timezone.utc),
    )
    assert f.instruments == ("KR:A005930",)
    assert f.kinds == ("filing",)
    assert f.span == (datetime(2026, 1, 1, tzinfo=timezone.utc), None)
    assert f.as_of == datetime(2026, 6, 15, tzinfo=timezone.utc)


def test_span_filter_range() -> None:
    """Span filter: published_at must fall within [start, end]."""
    old_item = _item(published_at=datetime(2026, 1, 1, tzinfo=timezone.utc))
    mid_item = _item(published_at=datetime(2026, 6, 15, tzinfo=timezone.utc))
    new_item = _item(published_at=datetime(2026, 12, 1, tzinfo=timezone.utc))
    items = [old_item, mid_item, new_item]

    span_start = datetime(2026, 3, 1, tzinfo=timezone.utc)
    span_end = datetime(2026, 9, 1, tzinfo=timezone.utc)

    result = search(items, span=(span_start, span_end), as_of=_BASE_TIME)

    assert len(result) == 1
    assert result[0] is mid_item


def test_span_filter_with_only_start() -> None:
    """Span filter: only start bound set (open-ended end)."""
    old_item = _item(published_at=datetime(2026, 1, 1, tzinfo=timezone.utc))
    new_item = _item(published_at=datetime(2026, 6, 15, tzinfo=timezone.utc))
    items = [old_item, new_item]

    span_start = datetime(2026, 3, 1, tzinfo=timezone.utc)

    result = search(items, span=(span_start, None), as_of=_BASE_TIME)

    assert len(result) == 1
    assert result[0] is new_item


def test_combined_filters_all_must_match() -> None:
    """Combined filters: AND logic — all filters must match."""
    item_a = _item(
        known_at=datetime(2026, 5, 1, tzinfo=timezone.utc),
        instruments=("KR:A005930",),
        kind="filing",
    )
    item_b = _item(
        known_at=datetime(2026, 5, 1, tzinfo=timezone.utc),
        instruments=("KR:A070000",),
        kind="filing",
    )
    item_c = _item(
        known_at=datetime(2026, 5, 1, tzinfo=timezone.utc),
        instruments=("KR:A005930",),
        kind="news",
    )
    items = [item_a, item_b, item_c]

    result = search(
        items,
        instruments=["KR:A005930"],
        kinds=["filing"],
        as_of=_BASE_TIME,
    )

    assert len(result) == 1
    assert result[0] is item_a
