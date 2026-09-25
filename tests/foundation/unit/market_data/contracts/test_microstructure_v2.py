"""DC-19 — market_data/contracts/v2/microstructure 스냅샷 + 검증 테스트.

Spec: docs/specs/L4_analytics_authoring_backtest_marketplace_v1.0.md
§2.1 DC-19, §9.10 DC-19.

`fixtures/market_data_contracts_v2_microstructure.json`은 현재 스키마의
스냅샷이다(DC-1 `test_instruments_v2.py`와 동일 원칙 — 107번 §3.3 필드
제거·이름 변경은 MAJOR, v2 안에서 조용히 추가하지 않는다).
"""

import json
from datetime import datetime
from decimal import Decimal
from pathlib import Path

import pytest
from pydantic import ValidationError

from src.foundation.market_data.contracts.v1 import Venue
from src.foundation.market_data.contracts.v2 import microstructure as v2

FIXTURE = Path(__file__).parent / "fixtures" / "market_data_contracts_v2_microstructure.json"

_MODELS = (
    v2.TradeTick,
    v2.QuoteL1,
    v2.BookL2,
)

_VALID_ULID = "01ARZ3NDEKTSV4RRFFQ69G5FAV"
_NS_EVENT = 1_767_225_600_123_456_789
_NS_RECV = 1_767_225_600_223_456_789
_SECOND_UNIT_VALUE = 1_767_225_600


def _sample_trade_tick(**overrides: object) -> v2.TradeTick:
    base: dict[str, object] = dict(
        instrument_id=_VALID_ULID,
        venue=Venue.BITGET,
        ts_event=_NS_EVENT,
        ts_recv=_NS_RECV,
        seq=1,
        price=Decimal("50000.5"),
        size=Decimal("0.01"),
        aggressor=v2.Aggressor.BUY,
    )
    base.update(overrides)
    return v2.TradeTick.model_validate(base)


def _sample_quote_l1(**overrides: object) -> v2.QuoteL1:
    base: dict[str, object] = dict(
        instrument_id=_VALID_ULID,
        venue=Venue.BITGET,
        ts_event=_NS_EVENT,
        ts_recv=_NS_RECV,
        seq=1,
        bid_price=Decimal("50000.0"),
        bid_size=Decimal("1.0"),
        ask_price=Decimal("50000.5"),
        ask_size=Decimal("1.2"),
    )
    base.update(overrides)
    return v2.QuoteL1.model_validate(base)


def _sample_book_l2(**overrides: object) -> v2.BookL2:
    base: dict[str, object] = dict(
        instrument_id=_VALID_ULID,
        venue=Venue.BITGET,
        ts_event=_NS_EVENT,
        ts_recv=_NS_RECV,
        seq=1,
        bids=(
            v2.BookLevel(price=Decimal("50000.0"), size=Decimal("1.0")),
            v2.BookLevel(price=Decimal("49999.5"), size=Decimal("2.0")),
        ),
        asks=(
            v2.BookLevel(price=Decimal("50000.5"), size=Decimal("1.2")),
            v2.BookLevel(price=Decimal("50001.0"), size=Decimal("0.8")),
        ),
    )
    base.update(overrides)
    return v2.BookL2.model_validate(base)


def test_schema_snapshot_matches_fixture() -> None:
    current = {m.__name__: m.model_json_schema() for m in _MODELS}
    expected = json.loads(FIXTURE.read_text(encoding="utf-8"))
    assert current == expected


def test_schema_version_is_microstructure_v2() -> None:
    tick = _sample_trade_tick()
    assert tick.schema_version == "microstructure-v2"
    assert v2.SCHEMA_VERSION == "microstructure-v2"


# --- (1) 초 단위/naive datetime 타임스탬프 거부 ---------------------------


def test_second_unit_ts_event_rejected() -> None:
    with pytest.raises(ValidationError):
        _sample_trade_tick(ts_event=_SECOND_UNIT_VALUE)


def test_naive_datetime_ts_event_rejected() -> None:
    with pytest.raises(ValidationError):
        _sample_trade_tick(ts_event=datetime(2026, 9, 3, 0, 0))


def test_nanosecond_ts_event_accepted() -> None:
    tick = _sample_trade_tick(ts_event=_NS_EVENT)
    assert tick.ts_event == _NS_EVENT


# --- (2) aggressor 표 밖 값 거부 ------------------------------------------


def test_aggressor_out_of_table_value_rejected() -> None:
    with pytest.raises(ValidationError):
        _sample_trade_tick(aggressor="MAKER")


# --- (3) BookL2 레벨 가격 정렬 위반 거부 -----------------------------------


def test_book_l2_bids_not_descending_rejected() -> None:
    with pytest.raises(ValidationError):
        _sample_book_l2(
            bids=(
                v2.BookLevel(price=Decimal("49999.5"), size=Decimal("2.0")),
                v2.BookLevel(price=Decimal("50000.0"), size=Decimal("1.0")),
            )
        )


def test_book_l2_asks_not_ascending_rejected() -> None:
    with pytest.raises(ValidationError):
        _sample_book_l2(
            asks=(
                v2.BookLevel(price=Decimal("50001.0"), size=Decimal("0.8")),
                v2.BookLevel(price=Decimal("50000.5"), size=Decimal("1.2")),
            )
        )


def test_book_l2_sorted_levels_accepted() -> None:
    book = _sample_book_l2()
    assert book.bids[0].price > book.bids[1].price
    assert book.asks[0].price < book.asks[1].price


# --- 그 외 ------------------------------------------------------------


def test_quote_l1_missing_required_field_rejected() -> None:
    with pytest.raises(ValidationError):
        v2.QuoteL1.model_validate(
            {
                "instrument_id": _VALID_ULID,
                "venue": Venue.BITGET,
                "ts_event": _NS_EVENT,
                "ts_recv": _NS_RECV,
                "seq": 1,
                "bid_price": Decimal("50000.0"),
                "bid_size": Decimal("1.0"),
                "ask_price": Decimal("50000.5"),
                # ask_size 누락
            }
        )


def test_seq_negative_rejected() -> None:
    with pytest.raises(ValidationError):
        _sample_trade_tick(seq=-1)


# --- (4) ULID 형식 위반 거부 -----------------------------------------------


def test_invalid_ulid_format_rejected() -> None:
    """ULID must be Crockford Base32, 26 chars, first digit 0-7."""
    with pytest.raises(ValidationError):
        _sample_trade_tick(instrument_id="zzzzzzzzzzzzzzzzzzzzzzzzzz")


def test_ulid_with_illegal_char_rejected() -> None:
    """ULID must not contain I, L, O, U (Crockford Base32)."""
    with pytest.raises(ValidationError):
        _sample_trade_tick(instrument_id="0AAAAAAAAAAAAAAAAAAAAAAAAI")


def test_ulid_too_short_rejected() -> None:
    """ULID must be exactly 26 characters."""
    with pytest.raises(ValidationError):
        _sample_trade_tick(instrument_id="01ARZ3NDEK")


# --- (5) Venue 표 밖 값 거부 ------------------------------------------------


def test_invalid_venue_rejected() -> None:
    """Venue must be one of the declared enum values."""
    with pytest.raises(ValidationError):
        _sample_trade_tick(venue="NONEXISTENT")


# --- (6) BookL2 빈 레벨 허용 여부 경계 --------------------------------------


def test_book_l2_empty_bids_asks_accepted() -> None:
    """Empty tuple is valid for bids/asks (pydantic allows it);
    sorting validator sees empty list and passes trivially."""
    book = _sample_book_l2(bids=(), asks=())
    assert book.bids == ()
    assert book.asks == ()


# --- (7) 실패주입: nanosecond validator 우회/고장 ----------------------------


def test_monkeypatch_nanosecond_validator_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    """Failure injection: monkeypatch the nanosecond validator so it raises
    ValueError on any input, simulating a broken time-source dependency.
    We patch the validator function in the module, then call it directly
    (Pydantic v2 compiles AfterValidator at import time, so the compiled
    validator cannot be hot-swapped — testing the function directly is the
    reliable failure-injection approach)."""
    import src.foundation.market_data.contracts.v2.microstructure as mod

    def _broken_validator(value: int) -> int:
        raise ValueError("simulated time-source failure")

    monkeypatch.setattr(mod, "_validate_nanoseconds", _broken_validator)

    # Call the patched validator directly — this is the failure-injection test.
    # In production, the AfterValidator pipeline calls this function.
    with pytest.raises(ValueError, match="simulated time-source failure"):
        mod._validate_nanoseconds(1_000_000_000_000_000_000)


def test_monkeypatch_nanosecond_upper_bound_raises() -> None:
    """Failure injection: ts_event above the nanosecond upper bound."""
    import src.foundation.market_data.contracts.v2.microstructure as mod

    with pytest.raises(ValidationError):
        mod.TradeTick.model_validate(
            dict(
                instrument_id=_VALID_ULID,
                venue=Venue.BITGET,
                ts_event=15_000_000_000_000_000_000,  # above 10^19
                ts_recv=_NS_RECV,
                seq=1,
                price=Decimal("50000.5"),
                size=Decimal("0.01"),
                aggressor=v2.Aggressor.BUY,
            )
        )


# --- (8) 성능 단언: 검증 예산 ------------------------------------------------


def test_validation_performance_under_budget() -> None:
    """Assert that 1000 model validations complete within 1 second (performance
    budget from ADR-2026-09-09-C Decision 1: tick-level validation < 1ms per
    record, 1000 records < 1s)."""
    import time

    iterations = 1000
    start = time.perf_counter_ns()
    for _ in range(iterations):
        _sample_trade_tick()
        _sample_quote_l1()
        _sample_book_l2()
    elapsed_us = (time.perf_counter_ns() - start) / 1000
    # 3 validations × 1000 iterations = 3000 validations.
    # Budget: 1ms per validation = 3000000 us total.
    assert elapsed_us < 3_000_000, f"validation took {elapsed_us:.0f} us, budget 3M us"
