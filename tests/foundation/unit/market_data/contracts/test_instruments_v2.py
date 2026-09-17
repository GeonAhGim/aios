"""DC-1 — market_data/contracts/v2/instruments 스냅샷 + 검증 테스트.

Spec: docs/specs/L4_analytics_authoring_backtest_marketplace_v1.0.md
§2.1 DC-1, §3.2(심볼 마스터), §9.2 DC-1.

`fixtures/market_data_contracts_v2_instruments.json`은 현재 스키마의
스냅샷이다. 필드를 지우거나 이름을 바꾸면 이 테스트가 즉시 실패한다(107번
§3.3 "필드 제거·이름 변경은 MAJOR"). 필드 추가는 새 MAJOR 버전(`v3`)이
필요하다 — v2 안에서 조용히 추가하지 않는다. QA는 이 파일로 §3.2 계약을
대조한다.

DEEPEN 1123 (task-2874, docs/audit/DEPTH_DC_RD.md): the original DC-1 leaf
graded D1 for missing failure injection, numeric performance assertions, a
gate/CI red-regression test, and D3 adversarial/multi-instance/replay proof.
This module is pure (no I/O, no mapper function), so those four are adapted
to what a pure pydantic-contract module can actually exercise:
  - failure injection: monkeypatch the ULID validator's pattern to simulate
    a future regression and prove `Instrument` construction still fails
    closed instead of silently accepting an unvalidated id
    (`test_ulid_validator_failure_injection_fails_closed`).
  - numeric performance: a wall-clock ceiling on constructing 5,000
    `Instrument` records (`test_bulk_instrument_construction...`).
  - gate/CI red regression: the DC-1 §3.2 required-field set must stay
    exactly what the spec table lists — a future edit that silently
    widens or narrows it fails this test red
    (`test_instrument_required_fields_match_dc1_contract_table_ci_guard`).
  - D3 adversarial + multi-instance/replay: frozen-model tamper rejection,
    and byte-identical output from independent OS processes given the same
    input (`test_frozen_...`, `test_replay_across_independent_processes...`).
"""

import json
import time
from concurrent.futures import ProcessPoolExecutor
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, cast

import pytest
from pydantic import ValidationError

from src.data.models.base import AssetClass
from src.foundation.market_data.contracts.v1 import Venue
from src.foundation.market_data.contracts.v2 import instruments as v2

FIXTURE = Path(__file__).parent / "fixtures" / "market_data_contracts_v2_instruments.json"

_MODELS = (
    v2.Instrument,
    v2.VenueListing,
)

_VALID_ULID = "01ARZ3NDEKTSV4RRFFQ69G5FAV"

# Crockford base32 alphabet (excludes I, L, O, U per the ULID spec) — used
# to generate distinct valid ULIDs for the bulk-construction perf test.
_CROCKFORD_ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"


def _ulid_for(index: int) -> str:
    digits = []
    remaining = index
    for _ in range(6):
        digits.append(_CROCKFORD_ALPHABET[remaining % 32])
        remaining //= 32
    suffix = "".join(reversed(digits))
    return _VALID_ULID[:20] + suffix


def _now() -> datetime:
    return datetime(2026, 9, 3, 0, 0, tzinfo=timezone.utc)


def _sample_instrument(**overrides: object) -> v2.Instrument:
    base: dict[str, object] = dict(
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
    return v2.Instrument(**base)  # type: ignore[arg-type]


def _sample_listing(**overrides: object) -> v2.VenueListing:
    base: dict[str, object] = dict(
        instrument_id=_VALID_ULID,
        venue=Venue.BITGET,
        venue_symbol="BTCUSDT",
        listed_at=_now(),
        delisted_at=None,
        is_primary=True,
    )
    base.update(overrides)
    return v2.VenueListing(**base)  # type: ignore[arg-type]


def test_schema_snapshot_matches_fixture() -> None:
    current = {m.__name__: m.model_json_schema() for m in _MODELS}
    expected = json.loads(FIXTURE.read_text(encoding="utf-8"))
    assert current == expected


def test_instrument_schema_version_is_instruments_v2() -> None:
    instrument = _sample_instrument()
    assert instrument.schema_version == "instruments-v2"
    assert v2.SCHEMA_VERSION == "instruments-v2"


def test_instrument_naive_created_at_rejected() -> None:
    with pytest.raises(ValidationError):
        _sample_instrument(created_at=datetime(2026, 9, 3, 0, 0))


def test_instrument_invalid_ulid_rejected() -> None:
    with pytest.raises(ValidationError):
        _sample_instrument(instrument_id="not-a-ulid")


def test_instrument_ulid_normalized_to_uppercase() -> None:
    instrument = _sample_instrument(instrument_id=_VALID_ULID.lower())
    assert instrument.instrument_id == _VALID_ULID


def test_instrument_missing_required_field_rejected() -> None:
    with pytest.raises(ValidationError):
        v2.Instrument(  # type: ignore[call-arg]
            instrument_id=_VALID_ULID,
            asset_class=AssetClass.CRYPTO,
            base="BTC",
            quote="USDT",
            isin=None,
            figi=None,
            tick_size=Decimal("0.01"),
            lot_size=Decimal("0.0001"),
            calendar_id="24x7",
            # lifecycle_state 누락
            created_at=_now(),
        )


def test_instrument_optional_identifiers_accept_none() -> None:
    instrument = _sample_instrument(isin=None, figi=None)
    assert instrument.isin is None
    assert instrument.figi is None


def test_venue_listing_naive_listed_at_rejected() -> None:
    with pytest.raises(ValidationError):
        _sample_listing(listed_at=datetime(2026, 9, 3, 0, 0))


def test_venue_listing_invalid_ulid_rejected() -> None:
    with pytest.raises(ValidationError):
        _sample_listing(instrument_id="0000")


def test_venue_listing_delisted_at_optional() -> None:
    listing = _sample_listing()
    assert listing.delisted_at is None

    delisted = _sample_listing(delisted_at=_now())
    assert delisted.delisted_at == _now()


def test_venue_listing_is_primary_required() -> None:
    with pytest.raises(ValidationError):
        v2.VenueListing(  # type: ignore[call-arg]
            instrument_id=_VALID_ULID,
            venue=Venue.BITGET,
            venue_symbol="BTCUSDT",
            listed_at=_now(),
            delisted_at=None,
            # is_primary 누락
        )


def test_venue_listing_invalid_venue_enum_rejected() -> None:
    with pytest.raises(ValidationError):
        _sample_listing(venue="NOT_A_VENUE")


def test_venue_listing_venue_symbol_wrong_type_rejected() -> None:
    with pytest.raises(ValidationError):
        _sample_listing(venue_symbol=12345)


def test_venue_listing_delisted_at_naive_datetime_rejected() -> None:
    """`delisted_at`은 optional이지만 값이 있을 때는 `listed_at`과 같은
    AwareDatetime 제약을 받는다 — naive 값을 조용히 허용하면 §4.1의
    tz-aware 불변조건이 optional 필드에서만 구멍난다."""
    with pytest.raises(ValidationError):
        _sample_listing(delisted_at=datetime(2026, 9, 3, 0, 0))


# --- DC-20: derivative-symbol fields (kind/underlying_id/expiry/strike/
# option_right/contract_multiplier/settlement/currency/country/mic) ---

_UNDERLYING_ULID = "01ARZ3NDEKTSV4RRFFQ69G5FB0"


def test_spot_instrument_unchanged_without_derivative_fields() -> None:
    instrument = _sample_instrument()
    assert instrument.kind is None
    assert instrument.underlying_id is None
    assert instrument.expiry is None
    assert instrument.strike is None
    assert instrument.option_right is None
    assert instrument.contract_multiplier is None
    assert instrument.settlement is None
    assert instrument.currency is None
    assert instrument.country is None
    assert instrument.mic is None


def test_option_instrument_creation() -> None:
    expiry = datetime(2026, 12, 18, 8, 30, tzinfo=timezone.utc)
    option = _sample_instrument(
        instrument_id="01ARZ3NDEKTSV4RRFFQ69G5FB1",
        kind=v2.InstrumentKind.OPTION,
        underlying_id=_UNDERLYING_ULID,
        expiry=expiry,
        strike=Decimal("70000"),
        option_right=v2.OptionRight.CALL,
        contract_multiplier=Decimal("1"),
        settlement=v2.SettlementType.CASH,
        currency="USD",
        country="US",
        mic="XNAS",
    )
    assert option.kind == v2.InstrumentKind.OPTION
    assert option.underlying_id == _UNDERLYING_ULID
    assert option.expiry == expiry
    assert option.strike == Decimal("70000")
    assert option.option_right == v2.OptionRight.CALL
    assert option.contract_multiplier == Decimal("1")
    assert option.settlement == v2.SettlementType.CASH
    assert option.currency == "USD"
    assert option.country == "US"
    assert option.mic == "XNAS"


def test_future_instrument_creation() -> None:
    expiry = datetime(2026, 9, 26, 8, 30, tzinfo=timezone.utc)
    future = _sample_instrument(
        instrument_id="01ARZ3NDEKTSV4RRFFQ69G5FB2",
        kind=v2.InstrumentKind.FUTURE,
        underlying_id=_UNDERLYING_ULID,
        expiry=expiry,
        contract_multiplier=Decimal("5"),
        settlement=v2.SettlementType.PHYSICAL,
        currency="KRW",
        country="KR",
        mic="XKRX",
    )
    assert future.kind == v2.InstrumentKind.FUTURE
    assert future.underlying_id == _UNDERLYING_ULID
    assert future.expiry == expiry
    assert future.contract_multiplier == Decimal("5")
    assert future.settlement == v2.SettlementType.PHYSICAL
    # strike/option_right stay unset for a future (only meaningful for options)
    assert future.strike is None
    assert future.option_right is None


def test_underlying_id_and_expiry_chain_query() -> None:
    near_expiry = datetime(2026, 9, 26, 8, 30, tzinfo=timezone.utc)
    far_expiry = datetime(2026, 12, 18, 8, 30, tzinfo=timezone.utc)
    chain = [
        _sample_instrument(
            instrument_id="01ARZ3NDEKTSV4RRFFQ69G5FB3",
            kind=v2.InstrumentKind.OPTION,
            underlying_id=_UNDERLYING_ULID,
            expiry=near_expiry,
            strike=Decimal("65000"),
            option_right=v2.OptionRight.CALL,
        ),
        _sample_instrument(
            instrument_id="01ARZ3NDEKTSV4RRFFQ69G5FB4",
            kind=v2.InstrumentKind.OPTION,
            underlying_id=_UNDERLYING_ULID,
            expiry=far_expiry,
            strike=Decimal("70000"),
            option_right=v2.OptionRight.CALL,
        ),
        # Different underlying — must not appear in the chain query below.
        _sample_instrument(
            instrument_id="01ARZ3NDEKTSV4RRFFQ69G5FB5",
            kind=v2.InstrumentKind.OPTION,
            underlying_id="01ARZ3NDEKTSV4RRFFQ69G5FB6",
            expiry=near_expiry,
            strike=Decimal("65000"),
            option_right=v2.OptionRight.CALL,
        ),
    ]

    matches = [
        instrument
        for instrument in chain
        if instrument.underlying_id == _UNDERLYING_ULID and instrument.expiry == near_expiry
    ]

    assert [m.instrument_id for m in matches] == ["01ARZ3NDEKTSV4RRFFQ69G5FB3"]


def test_instrument_invalid_currency_rejected() -> None:
    with pytest.raises(ValidationError):
        _sample_instrument(currency="US")


def test_instrument_invalid_country_rejected() -> None:
    with pytest.raises(ValidationError):
        _sample_instrument(country="USA")


def test_instrument_invalid_mic_rejected() -> None:
    with pytest.raises(ValidationError):
        _sample_instrument(mic="XN")


def test_instrument_currency_country_mic_normalized_to_uppercase() -> None:
    instrument = _sample_instrument(currency="usd", country="us", mic="xnas")
    assert instrument.currency == "USD"
    assert instrument.country == "US"
    assert instrument.mic == "XNAS"


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

    listing = _sample_listing()
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
        _sample_listing()


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


def test_bulk_instrument_construction_completes_within_latency_budget() -> None:
    """Numeric performance assertion: constructing a large symbol-master
    page must not become a bottleneck for callers (DC-2 symbol_master,
    DC-5 ports). 5,000 instruments is far more than the venue/instrument
    count AIOS is provisioned for today; the budget is a generous ceiling
    on frozen-model construction + validation cost, not a copy of any
    specific SLO.
    """
    started = time.perf_counter()
    instruments = [_sample_instrument(instrument_id=_ulid_for(i)) for i in range(5000)]
    elapsed_s = time.perf_counter() - started

    assert len(instruments) == 5000
    assert elapsed_s < 2.0, f"constructing 5,000 Instruments took {elapsed_s:.3f}s (budget 2.0s)"


def test_instrument_required_fields_match_dc1_contract_table_ci_guard() -> None:
    """Gate/CI red regression: DC-1's §3.2 field table is fixed. If a
    future edit makes any DC-20 derivative field (`kind`, `underlying_id`,
    ...) required, or adds a new required field outside the table without
    bumping to a `v3` module (107 §3.3 — a newly-required field is a MAJOR
    change just as a removed one is), this guard trips CI red immediately
    instead of surfacing as a silent deserialization failure for existing
    rows written under the old (optional) shape.
    """
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
