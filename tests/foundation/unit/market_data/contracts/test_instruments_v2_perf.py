"""DC-1 perf — instruments v2: failure injection, numeric performance, gate-red, replay.

Spec: docs/specs/L4_analytics_authoring_backtest_marketplace_v1.0.md
§2.1 DC-1, §9.2 DC-1.

Supplements test_instruments_v2.py with D3 adversarial, failure injection,
numeric performance, gate/CI red regression, and multi-instance/replay tests.
"""

from concurrent.futures import ProcessPoolExecutor
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any, cast

import pytest
from pydantic import ValidationError

from src.data.models.base import AssetClass
from src.foundation.market_data.contracts.v2 import instruments as v2
from tests.conftest import PerfBudget
from tests.support.perf_inject import virtual_delayed_sync

_VALID_ULID = "01ARZ3NDEKTSV4RRFFQ69G5FAV"

_BULK_BUDGET_MS = 2_000.0  # 2.0s — budget 값 불변


def _now() -> datetime:
    return datetime(2026, 9, 3, 0, 0, tzinfo=UTC)


def _sample_instrument(**overrides: object) -> v2.Instrument:
    base: dict[str, Any] = dict(
        instrument_id=_VALID_ULID,
        asset_class=AssetClass.CRYPTO,
        base="BTC",
        quote="USDT",
        isin=None,
        figi=None,
        tick_size=Decimal("0.01"),
        lot_size=Decimal("0.0001"),
        calendar_id="24x7",
        lifecycle_state=v2.InstrumentLifecycle.ACTIVE,
        created_at=_now(),
    )
    base.update(overrides)
    return v2.Instrument(**cast(dict[str, Any], base))


# --- DEEPEN 1123 (task-2874): D3 adversarial, failure injection, numeric
# performance, gate/CI red regression, multi-instance/replay ---


def test_frozen_instrument_and_venue_listing_reject_post_construction_tampering() -> None:
    """D3 adversarial: an in-memory `Instrument`/`VenueListing` must not be
    mutable after construction — flipping `lifecycle_state` or swapping
    `instrument_id` downstream (a bug or an attacker) must be a
    `ValidationError`, not a silent attribute assignment. Mirrors every
    other contract in this package (`candle_lineage.TickLineage`,
    `coverage.CoverageSpan`, `microstructure.TradeTick`/`QuoteL1`/`BookL2`),
    all of which are already `frozen=True`.
    """
    instrument = _sample_instrument()
    with pytest.raises(ValidationError):
        cast(Any, instrument).lifecycle_state = v2.InstrumentLifecycle.DELISTED
    with pytest.raises(ValidationError):
        cast(Any, instrument).instrument_id = "01ARZ3NDEKTSV4RRFFQ69G5FB9"

    listing = v2.VenueListing(
        instrument_id=_VALID_ULID,
        venue=cast(Any, v2).Venue.BITGET,
        venue_symbol="BTCUSDT",
        listed_at=_now(),
        delisted_at=None,
        is_primary=True,
    )
    with pytest.raises(ValidationError):
        cast(Any, listing).is_primary = False


def test_ulid_validator_failure_injection_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    """Failure injection: if the ULID validator's pattern breaks (e.g. a
    future refactor leaves `_ULID_PATTERN` unset), `Instrument`/
    `VenueListing` construction must still fail closed — never silently
    accept an unvalidated `instrument_id`. `AfterValidator` runs
    `_validate_ulid` unconditionally on every construction, so a broken
    pattern surfaces as an exception immediately rather than producing a
    half-validated record.
    """
    monkeypatch.setattr(v2, "_ULID_PATTERN", None)
    with pytest.raises(AttributeError):
        _sample_instrument()
    with pytest.raises(AttributeError):
        v2.VenueListing(
            instrument_id=_VALID_ULID,
            venue=cast(Any, v2).Venue.BITGET,
            venue_symbol="BTCUSDT",
            listed_at=_now(),
            delisted_at=None,
            is_primary=True,
        )


def test_currency_country_mic_validator_failure_injection_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Same failure-injection guarantee as the ULID pattern above, for the
    DC-20 `currency`/`country`/`mic` format validators: if a future refactor
    leaves one of `_CURRENCY_PATTERN`/`_COUNTRY_PATTERN`/`_MIC_PATTERN`
    unset, construction must fail closed (`AttributeError` from
    `AfterValidator`) rather than silently accepting an unvalidated code."""
    monkeypatch.setattr(v2, "_CURRENCY_PATTERN", None)
    with pytest.raises(AttributeError):
        _sample_instrument(currency="USD")

    monkeypatch.setattr(v2, "_COUNTRY_PATTERN", None)
    with pytest.raises(AttributeError):
        _sample_instrument(country="US")

    monkeypatch.setattr(v2, "_MIC_PATTERN", None)
    with pytest.raises(AttributeError):
        _sample_instrument(mic="XNAS")


# --- Negative-path latency budget (best_of + gate-red) ---

_NOW_PATH = "tests.foundation.unit.market_data.contracts.test_instruments_v2_perf._now"


@pytest.mark.perf
def test_instrument_naive_created_at_rejected_within_budget(perf_budget: PerfBudget) -> None:
    """Negative test: naive datetime is rejected, and rejection stays under 1 ms CPU.
    best_of avoids Windows CPU-quantisation noise."""
    with pytest.raises(ValidationError):
        _sample_instrument(created_at=datetime(2026, 9, 3, 0, 0))
    sample = perf_budget.best_of(
        lambda: pytest.raises(
            ValidationError, lambda: _sample_instrument(created_at=datetime(2026, 9, 3, 0, 0))
        ),
        n=5,
    )
    assert sample.cpu_ms < 1.0, f"naive datetime rejection took {sample.cpu_ms:.3f}ms (budget 1ms)"


@pytest.mark.perf
def test_instrument_naive_created_at_virtual_delay_trips_budget(
    perf_budget: PerfBudget, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Gate-red reproduction: 5 ms virtual CPU delay in `_now` trips the budget."""
    monkeypatch.setattr(
        "tests.foundation.unit.market_data.contracts.test_instruments_v2_perf._now",
        virtual_delayed_sync(_now, 0.005),
    )
    with pytest.raises(AssertionError, match="budget 1ms"):
        test_instrument_naive_created_at_rejected_within_budget(perf_budget)


@pytest.mark.perf
def test_instrument_invalid_ulid_rejected_within_budget(perf_budget: PerfBudget) -> None:
    """Negative test: malformed ULID is rejected, and rejection stays under 1 ms CPU."""
    with pytest.raises(ValidationError):
        _sample_instrument(instrument_id="not-a-ulid")
    sample = perf_budget.best_of(
        lambda: pytest.raises(
            ValidationError, lambda: _sample_instrument(instrument_id="not-a-ulid")
        ),
        n=5,
    )
    assert sample.cpu_ms < 1.0, f"invalid ULID rejection took {sample.cpu_ms:.3f}ms (budget 1ms)"


@pytest.mark.perf
def test_instrument_invalid_ulid_virtual_delay_trips_budget(
    perf_budget: PerfBudget, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Gate-red reproduction: 5 ms virtual CPU delay in `_now` trips the budget."""
    monkeypatch.setattr(
        "tests.foundation.unit.market_data.contracts.test_instruments_v2_perf._now",
        virtual_delayed_sync(_now, 0.005),
    )
    with pytest.raises(AssertionError, match="budget 1ms"):
        test_instrument_invalid_ulid_rejected_within_budget(perf_budget)


# --- Numeric performance: bulk construction ---


def _build_bulk_instruments() -> list[v2.Instrument]:
    """Generate 5,000 distinct `Instrument` instances for bulk perf measurement."""
    _CROCKFORD_ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
    base_ulid = "01ARZ3NDEKTSV4RRFFQ69G5FAV"
    base_id = base_ulid[:20]
    instruments: list[v2.Instrument] = []
    for i in range(5000):
        digits = []
        remaining = i
        for _ in range(6):
            digits.append(_CROCKFORD_ALPHABET[remaining % 32])
            remaining //= 32
        suffix = "".join(reversed(digits))
        instruments.append(_sample_instrument(instrument_id=base_id + suffix))
    return instruments


def _assert_bulk_construction_within_budget(perf_budget: PerfBudget, build: Any) -> None:
    # best_of (min of several runs) keeps one coarse Windows process_time tick from skewing it.
    sample = perf_budget.best_of(build, n=5, warmup=1, batch=1)

    assert len(sample.result) == 5000
    assert sample.cpu_ms < _BULK_BUDGET_MS, (
        f"constructing 5,000 Instruments took {sample.cpu_ms:.1f}ms "
        f"(budget {_BULK_BUDGET_MS:.0f}ms)"
    )


@pytest.mark.perf
def test_bulk_instrument_construction_completes_within_latency_budget(
    perf_budget: PerfBudget,
) -> None:
    """task-10978: raw time.perf_counter() → perf_budget.sample() 전환.
    process_time 기반 측정으로 부하 민감 호스트에서 다른 프로세스에 코어를
    뺏겨 대기한 시간이 wall-clock에 섞이지 않게 한다.
    """
    _assert_bulk_construction_within_budget(perf_budget, _build_bulk_instruments)


@pytest.mark.perf
def test_bulk_instrument_construction_budget_assertion_catches_10x_virtual_delay(
    perf_budget: PerfBudget,
) -> None:
    """Gate-red reproduction: a virtual 10× budget delay trips the same
    assertion (no real sleep)."""
    from tests.support.perf_inject import virtual_delayed_sync

    delayed = virtual_delayed_sync(_build_bulk_instruments, seconds=_BULK_BUDGET_MS * 10 / 1000)
    with pytest.raises(AssertionError, match="budget 2000ms"):
        _assert_bulk_construction_within_budget(perf_budget, delayed)


# --- Gate/CI red regression: required fields must match spec ---


def test_instrument_required_fields_match_dc1_contract_table_ci_guard() -> None:
    """DC-1 §3.2 required-field set must stay exactly what the spec table
    lists — a future edit that silently widens or narrows it fails
    this test red."""
    expected_required = {
        "instrument_id",
        "asset_class",
        "base",
        "quote",
        "isin",
        "figi",
        "tick_size",
        "lot_size",
        "calendar_id",
        "lifecycle_state",
        "created_at",
    }
    actual_required = {
        name for name, field in v2.Instrument.model_fields.items() if field.is_required()
    }
    assert actual_required == expected_required


# --- D3 multi-instance/replay proof ---


def _replay_instrument_in_subprocess() -> str:
    """Module-level so it is picklable for `ProcessPoolExecutor` on
    Windows (spawn start method)."""
    instrument = v2.Instrument(
        instrument_id=_VALID_ULID,
        asset_class=AssetClass.CRYPTO,
        base="BTC",
        quote="USDT",
        isin=None,
        figi=None,
        tick_size=Decimal("0.01"),
        lot_size=Decimal("0.0001"),
        calendar_id="24x7",
        lifecycle_state=v2.InstrumentLifecycle.ACTIVE,
        created_at=_now(),
    )
    return instrument.model_dump_json()


def test_replay_across_independent_processes_is_byte_identical() -> None:
    """D3 multi-instance/replay proof: three independent OS processes,
    each constructing the same `Instrument` from the same literal input,
    must produce byte-identical serialized output — no process-local
    cache or import-order nondeterminism leaking into the symbol master.
    """
    with ProcessPoolExecutor(max_workers=3) as pool:
        futures = [pool.submit(_replay_instrument_in_subprocess) for _ in range(3)]
        results = [future.result() for future in futures]

    assert len(results) == 3
    assert len(set(results)) == 1
