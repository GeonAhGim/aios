"""UX-6 `application/run_screen.py` unit tests (fake ports, no DB).

Spec: docs/specs/L4_product_experience_and_discovery_v1.0.md §9 UX-6
DoD("1,000 rows <=10s, cross-tenant 404"), ADR-2026-09-09-C Decision 1
(per-axis performance budgets, "스크리너 5k 심볼 2초").
"""

from __future__ import annotations

import asyncio
import re
import time
from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from uuid import UUID, uuid4

import pytest

from src.data.models.base import AssetClass
from src.foundation.market_data.contracts.v1 import InstrumentRef, SymbolStatus, Venue
from src.foundation.screener.application.run_screen import (
    MAX_RESULT_ROWS,
    ScreenLimitExceededError,
    ScreenResultCache,
    ScreenTimeoutError,
    ScreenUniverseError,
    run_saved_screen,
    run_screen,
)
from src.foundation.screener.contracts.v1 import (
    IndicatorFilter,
    ScreenDefinition,
    SortSpec,
)
from src.foundation.screener.domain.evaluate import ScreenerEvaluationError

_LISTED_AT = datetime(2020, 1, 1, tzinfo=timezone.utc)


def _instrument(instrument_id: UUID, *, venue: Venue = Venue.KIS_KRX, symbol: str) -> InstrumentRef:
    return InstrumentRef(
        instrument_id=instrument_id,
        venue=venue,
        canonical_symbol=symbol,
        venue_symbol=symbol,
        asset_class=AssetClass.KR_EQUITY,
        base=None,
        quote=None,
        tick_size=Decimal("1"),
        lot_size=Decimal("1"),
        status=SymbolStatus.LISTED,
        listed_at=_LISTED_AT,
        delisted_at=None,
    )


class FakeFieldSource:
    """In-memory `ScreenerFieldSource` — records call counts so tests can prove
    the result cache actually skips re-scanning the universe."""

    def __init__(
        self,
        instruments: Sequence[InstrumentRef],
        fields: Mapping[UUID, Mapping[str, Decimal]],
        *,
        delay_seconds: float = 0.0,
    ) -> None:
        self._instruments = sorted(
            instruments, key=lambda i: (i.venue.value, i.canonical_symbol, str(i.instrument_id))
        )
        self._fields = fields
        self._delay_seconds = delay_seconds
        self.universe_page_calls = 0
        self.read_fields_calls = 0

    async def universe_page(
        self, *, venues: frozenset[Venue], after: UUID | None, limit: int
    ) -> list[InstrumentRef]:
        self.universe_page_calls += 1
        if self._delay_seconds:
            await asyncio.sleep(self._delay_seconds)
        pool = [i for i in self._instruments if i.venue in venues]
        if after is not None:
            idx = next((k for k, i in enumerate(pool) if i.instrument_id == after), len(pool))
            pool = pool[idx + 1 :]
        return pool[:limit]

    async def read_fields(
        self,
        *,
        instrument_ids_by_venue: Mapping[Venue, Sequence[UUID]],
        field_names: frozenset[str],
        as_of: datetime,
    ) -> dict[UUID, dict[str, Decimal]]:
        self.read_fields_calls += 1
        result: dict[UUID, dict[str, Decimal]] = {}
        for ids in instrument_ids_by_venue.values():
            for instrument_id in ids:
                row = self._fields.get(instrument_id)
                if row is None:
                    continue
                result[instrument_id] = {k: v for k, v in row.items() if k in field_names}
        return result


class FakeSavedScreenerRepository:
    def __init__(self) -> None:
        self._rows: dict[tuple[UUID, UUID], object] = {}

    async def save(self, *, tenant_id: UUID, name: str, definition: ScreenDefinition):
        from src.foundation.screener.contracts.v1 import SavedScreenerView

        now = datetime.now(timezone.utc)
        view = SavedScreenerView(
            id=uuid4(),
            tenant_id=tenant_id,
            name=name,
            definition=definition,
            created_at=now,
            updated_at=now,
        )
        self._rows[(tenant_id, view.id)] = view
        return view

    async def list_for_tenant(self, tenant_id: UUID):
        return tuple(v for (t, _), v in self._rows.items() if t == tenant_id)

    async def get(self, tenant_id: UUID, screener_id: UUID):
        return self._rows.get((tenant_id, screener_id))

    async def delete(self, tenant_id: UUID, screener_id: UUID) -> bool:
        return self._rows.pop((tenant_id, screener_id), None) is not None


def _definition(*, sort: SortSpec | None = None) -> ScreenDefinition:
    return ScreenDefinition(
        universe="KIS_KRX",
        filters=(IndicatorFilter(condition="close > 100"),),
        sort=sort,
    )


# ---- happy path: matching rows are returned, sorted ----


async def test_run_screen_returns_matching_rows_sorted() -> None:
    a, b = uuid4(), uuid4()
    instruments = [_instrument(a, symbol="AAA"), _instrument(b, symbol="BBB")]
    fields = {a: {"close": Decimal("50")}, b: {"close": Decimal("150")}}
    field_source = FakeFieldSource(instruments, fields)

    page = await run_screen(
        _definition(sort=SortSpec(field="close", direction="asc")),
        field_source=field_source,
        cache=ScreenResultCache(),
    )

    assert [r.instrument_id for r in page.rows] == [b]
    assert page.total == 1
    assert page.truncated is False


# ---- cursor pagination continues across pages ----


async def test_run_screen_pagination_cursor_continues() -> None:
    instruments = [_instrument(uuid4(), symbol=f"S{i:03d}") for i in range(5)]
    fields = {i.instrument_id: {"close": Decimal("200")} for i in instruments}
    field_source = FakeFieldSource(instruments, fields)
    cache = ScreenResultCache()

    first = await run_screen(_definition(), field_source=field_source, cache=cache, page_size=2)
    assert len(first.rows) == 2
    assert first.next_cursor == "2"

    second = await run_screen(
        _definition(), field_source=field_source, cache=cache, cursor=first.next_cursor, page_size=2
    )
    assert len(second.rows) == 2
    assert {r.instrument_id for r in first.rows}.isdisjoint({r.instrument_id for r in second.rows})


# ---- result cache: a second page for the same definition does not rescan ----


async def test_run_screen_reuses_cache_across_pages() -> None:
    instruments = [_instrument(uuid4(), symbol=f"S{i:03d}") for i in range(5)]
    fields = {i.instrument_id: {"close": Decimal("200")} for i in instruments}
    field_source = FakeFieldSource(instruments, fields)
    cache = ScreenResultCache()

    first = await run_screen(_definition(), field_source=field_source, cache=cache, page_size=2)
    calls_after_first = field_source.universe_page_calls
    await run_screen(
        _definition(), field_source=field_source, cache=cache, cursor=first.next_cursor, page_size=2
    )

    assert field_source.universe_page_calls == calls_after_first


# ---- row cap: capped at MAX_RESULT_ROWS and marked truncated ----


async def test_run_screen_caps_at_max_rows_and_marks_truncated() -> None:
    instruments = [_instrument(uuid4(), symbol=f"S{i:05d}") for i in range(MAX_RESULT_ROWS + 50)]
    fields = {i.instrument_id: {"close": Decimal("200")} for i in instruments}
    field_source = FakeFieldSource(instruments, fields)

    page = await run_screen(
        _definition(),
        field_source=field_source,
        cache=ScreenResultCache(),
        page_size=MAX_RESULT_ROWS,
    )

    assert page.total == MAX_RESULT_ROWS
    assert page.truncated is True


# ---- negative: unknown universe string ----


async def test_run_screen_rejects_unknown_universe() -> None:
    bad = ScreenDefinition(
        universe="MOON_EXCHANGE", filters=(IndicatorFilter(condition="close > 1"),)
    )
    with pytest.raises(ScreenUniverseError):
        await run_screen(bad, field_source=FakeFieldSource([], {}), cache=ScreenResultCache())


# ---- negative: unrecognized field name (no IND-12 registry yet) ----


async def test_run_screen_rejects_unknown_field() -> None:
    bad = ScreenDefinition(universe="KIS_KRX", filters=(IndicatorFilter(condition="per < 15"),))
    with pytest.raises(ScreenerEvaluationError):
        await run_screen(bad, field_source=FakeFieldSource([], {}), cache=ScreenResultCache())


# ---- negative: page_size outside [1, MAX_RESULT_ROWS] ----


async def test_run_screen_rejects_invalid_page_size() -> None:
    with pytest.raises(ScreenLimitExceededError):
        await run_screen(
            _definition(),
            field_source=FakeFieldSource([], {}),
            cache=ScreenResultCache(),
            page_size=0,
        )
    with pytest.raises(ScreenLimitExceededError):
        await run_screen(
            _definition(),
            field_source=FakeFieldSource([], {}),
            cache=ScreenResultCache(),
            page_size=MAX_RESULT_ROWS + 1,
        )


# ---- negative: cross-tenant saved-screener run is a 404, never another
# tenant's rows (§9 UX-6 DoD "cross-tenant 404") ----


async def test_run_saved_screen_cross_tenant_returns_none() -> None:
    repo = FakeSavedScreenerRepository()
    owner, stranger = uuid4(), uuid4()
    saved = await repo.save(tenant_id=owner, name="mine", definition=_definition())

    result = await run_saved_screen(
        stranger,
        saved.id,
        repo=repo,
        field_source=FakeFieldSource([], {}),
        cache=ScreenResultCache(),
    )

    assert result is None


async def test_run_saved_screen_owner_gets_a_result() -> None:
    repo = FakeSavedScreenerRepository()
    owner = uuid4()
    a = uuid4()
    saved = await repo.save(tenant_id=owner, name="mine", definition=_definition())
    field_source = FakeFieldSource([_instrument(a, symbol="AAA")], {a: {"close": Decimal("200")}})

    result = await run_saved_screen(
        owner, saved.id, repo=repo, field_source=field_source, cache=ScreenResultCache()
    )

    assert result is not None
    assert result.total == 1


# ---- failure injection: a slow/hung field source trips the §3 UX_SCREEN_TIMEOUT
# path instead of hanging the request indefinitely ----


async def test_run_screen_times_out_on_slow_field_source() -> None:
    instruments = [_instrument(uuid4(), symbol="AAA")]
    field_source = FakeFieldSource(instruments, {}, delay_seconds=0.2)

    with pytest.raises(ScreenTimeoutError):
        await run_screen(
            _definition(),
            field_source=field_source,
            cache=ScreenResultCache(),
            timeout_seconds=0.01,
        )


# ---- perf assertion: ADR-2026-09-09-C 축별 예산 "스크리너 5k 심볼 2초" ----


@pytest.mark.perf
async def test_run_screen_scans_5000_symbols_within_adr_budget() -> None:
    # No row matches ("close" always 50 <= 100) so the scan must walk the full
    # 5,000-symbol universe end to end instead of stopping early at the
    # MAX_RESULT_ROWS cap — that is what actually exercises the ADR throughput
    # budget below (a cap-triggered early exit would prove nothing about 5k).
    instruments = [_instrument(uuid4(), symbol=f"S{i:05d}") for i in range(5000)]
    fields = {i.instrument_id: {"close": Decimal("50")} for i in instruments}
    field_source = FakeFieldSource(instruments, fields)

    start = time.perf_counter()
    page = await run_screen(
        _definition(),
        field_source=field_source,
        cache=ScreenResultCache(),
        page_size=MAX_RESULT_ROWS,
    )
    elapsed = time.perf_counter() - start

    assert page.total == 0
    assert page.truncated is False
    assert elapsed < 2.0  # ADR-2026-09-09-C: "스크리너 5k 심볼 2초"


# ---- gate-red repro: prove the "no float() coercion" scanner used below would
# actually catch a violation, then prove the real source files are clean ----

_FLOAT_CAST_RE = re.compile(r"\bfloat\(")


def _float_cast_lines(source: str) -> list[str]:
    return [line for line in source.splitlines() if _FLOAT_CAST_RE.search(line)]


def test_gate_red_repro_float_cast_scanner_catches_injected_violation() -> None:
    violating_source = "value = float(row['close'])  # money must stay Decimal\n"
    assert _float_cast_lines(violating_source) != []


def test_run_screen_and_evaluate_have_no_float_coercion() -> None:
    repo_root = Path(__file__).resolve().parents[3]
    sources = [
        repo_root / "src/foundation/screener/domain/evaluate.py",
        repo_root / "src/foundation/screener/application/run_screen.py",
        repo_root / "src/foundation/screener/adapters/postgres_field_source.py",
    ]
    for path in sources:
        assert _float_cast_lines(path.read_text(encoding="utf-8")) == [], path
