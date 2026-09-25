"""RD-9 -- `adapters/dsl_query.py` `research.*` builtin table tests.

Spec: docs/specs/L4_research_data_and_market_ecosystem_v1.0.md §9 RD-9
("expose `research.*` queries to scripts with `as_of` auto-binding +
leakage tests"), §4 RD-A1.

Full D2 negative/failure-injection/perf/gate-red battery for this file and
`domain/as_of_binding.py` together is deferred to the follow-up leaf
recorded in task-2710's `decision`. This file covers the mandatory floor:
arity/shape rejection and the auto-binding behaviour end to end.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest

from src.core.script.runtime.interpreter_types import CallSite
from src.core.script.runtime.series import Series
from src.foundation.market_data.domain.candle_columns import CandleColumns
from src.foundation.research_data.adapters.dsl_query import (
    ResearchDslQueryError,
    research_builtins,
)
from src.foundation.research_data.contracts.v1 import ResearchItem

_INSTRUMENT = "KR:A005930"
_BASE = datetime(2026, 6, 1, tzinfo=timezone.utc)


def _item(*, known_at: datetime, kind: str = "filing") -> ResearchItem:
    return ResearchItem(
        item_id=uuid4(),
        source_id="test-source",
        kind=kind,
        published_at=known_at,
        known_at=known_at,
        instruments=(_INSTRUMENT,),
        title="test",
        body_ref="/test/ref",
        url="https://example.com/test",
        language="ko",
        hash="abc123",
        revision_of=None,
    )


def _columns(n: int) -> CandleColumns:
    from decimal import Decimal

    ts = [_BASE + timedelta(days=i) for i in range(n)]
    one = [Decimal("1")] * n
    return CandleColumns(
        ts=ts, open=one, high=one, low=one, close=one, volume=one, quote_volume=[None] * n
    )


def test_research_builtins_registers_all_kinds() -> None:
    table = research_builtins([], _columns(1), instrument=_INSTRUMENT)
    assert set(table) == {
        ("research", "filing_count"),
        ("research", "news_count"),
        ("research", "macro_count"),
        ("research", "alt_count"),
    }


def test_filing_count_auto_binds_per_bar() -> None:
    """RD-9: as_of auto-binds to each bar's own ts -- a filing known on
    day 2 is invisible on day 0/1 and visible from day 2 onward (RD-A1)."""
    items = [_item(known_at=_BASE + timedelta(days=2))]
    columns = _columns(4)
    table = research_builtins(items, columns, instrument=_INSTRUMENT)
    builtin = table[("research", "filing_count")]
    site = CallSite("research", "filing_count", "series<float>", 4)

    result = builtin((), site)

    assert isinstance(result, Series)
    assert [result.at(i) for i in range(4)] == [0.0, 0.0, 1.0, 1.0]


def test_rejects_arguments() -> None:
    """Negative: the grammar has no string literal, so this builtin is
    zero-arg only -- any argument is a caller bug, rejected fail-closed."""
    table = research_builtins([], _columns(1), instrument=_INSTRUMENT)
    builtin = table[("research", "filing_count")]
    site = CallSite("research", "filing_count", "series<float>", 1)

    with pytest.raises(ResearchDslQueryError, match="takes no arguments"):
        builtin((1.0,), site)


def test_rejects_bar_count_mismatch() -> None:
    """Negative: columns/bar_count disagreement is a caller bug (mirrors
    `ScriptSignalSourceError` in `script_signal_source.py`)."""
    table = research_builtins([], _columns(3), instrument=_INSTRUMENT)
    builtin = table[("research", "filing_count")]
    site = CallSite("research", "filing_count", "series<float>", 5)

    with pytest.raises(ResearchDslQueryError, match="differs from"):
        builtin((), site)


def test_other_instrument_items_excluded() -> None:
    """Negative: items for a different instrument never leak into the
    host-bound instrument's count."""
    other = ResearchItem(
        item_id=uuid4(),
        source_id="test-source",
        kind="filing",
        published_at=_BASE,
        known_at=_BASE,
        instruments=("KR:A000001",),
        title="test",
        body_ref="/test/ref",
        url="https://example.com/test",
        language="ko",
        hash="def456",
        revision_of=None,
    )
    columns = _columns(1)
    table = research_builtins([other], columns, instrument=_INSTRUMENT)
    builtin = table[("research", "filing_count")]
    site = CallSite("research", "filing_count", "series<float>", 1)

    result = builtin((), site)

    assert result.at(0) == 0.0


def test_kind_isolation() -> None:
    """Negative: a `news` item never counts toward `filing_count`."""
    items = [_item(known_at=_BASE, kind="news")]
    columns = _columns(1)
    table = research_builtins(items, columns, instrument=_INSTRUMENT)
    filing_builtin = table[("research", "filing_count")]
    news_builtin = table[("research", "news_count")]
    site = CallSite("research", "filing_count", "series<float>", 1)

    assert filing_builtin((), site).at(0) == 0.0
    assert news_builtin((), site).at(0) == 1.0
