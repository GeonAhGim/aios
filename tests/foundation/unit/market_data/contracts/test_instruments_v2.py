"""DC-1 — market_data/contracts/v2/instruments 스냅샷 + 검증 테스트.

Spec: docs/specs/L4_analytics_authoring_backtest_marketplace_v1.0.md
§2.1 DC-1, §3.2(심볼 마스터), §9.2 DC-1.

`fixtures/market_data_contracts_v2_instruments.json`은 현재 스키마의
스냅샷이다. 필드를 지우거나 이름을 바꾸면 이 테스트가 즉시 실패한다(107번
§3.3 "필드 제거·이름 변경은 MAJOR"). 필드 추가는 새 MAJOR 버전(`v3`)이
필요하다 — v2 안에서 조용히 추가하지 않는다. QA는 이 파일로 §3.2 계약을
대조한다.
"""
import json
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

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
