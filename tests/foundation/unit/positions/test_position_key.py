"""LB-2 — position_key 직렬화·파싱 단위 테스트.

Spec: docs/specs/L4_market_data_positions_ledger_v1.0.md#§9 LB-2
(`venue:instrument_id:strategy_id:execution_id` 4부분 형식).
"""
from __future__ import annotations

import pytest

from src.foundation.positions.domain.position_key import (
    InvalidPositionKeyError,
    PositionKey,
)


def test_str_serializes_four_parts_with_colon_delimiter() -> None:
    key = PositionKey(
        venue="bitget",
        instrument_id="BTC/USDT",
        strategy_id="strat-1",
        execution_id="exec-1",
    )
    assert str(key) == "bitget:BTC/USDT:strat-1:exec-1"


def test_parse_round_trips_with_str() -> None:
    raw = "bitget:BTC/USDT:strat-1:exec-1"
    key = PositionKey.parse(raw)
    assert key == PositionKey(
        venue="bitget", instrument_id="BTC/USDT", strategy_id="strat-1", execution_id="exec-1"
    )
    assert str(key) == raw


def test_parse_rejects_wrong_field_count() -> None:
    with pytest.raises(InvalidPositionKeyError):
        PositionKey.parse("bitget:BTC/USDT:strat-1")

    with pytest.raises(InvalidPositionKeyError):
        PositionKey.parse("bitget:BTC/USDT:strat-1:exec-1:extra")


def test_parse_rejects_empty_raw_string() -> None:
    with pytest.raises(InvalidPositionKeyError):
        PositionKey.parse("")


def test_construct_rejects_empty_component() -> None:
    with pytest.raises(InvalidPositionKeyError):
        PositionKey(
            venue="", instrument_id="BTC/USDT", strategy_id="strat-1", execution_id="exec-1"
        )


def test_construct_rejects_component_containing_delimiter() -> None:
    with pytest.raises(InvalidPositionKeyError):
        PositionKey(
            venue="bitget:extra",
            instrument_id="BTC/USDT",
            strategy_id="strat-1",
            execution_id="exec-1",
        )


def test_position_key_is_frozen_and_hashable() -> None:
    key = PositionKey(
        venue="bitget", instrument_id="BTC/USDT", strategy_id="strat-1", execution_id="exec-1"
    )
    with pytest.raises(AttributeError):
        key.venue = "kis"  # type: ignore[misc]
    assert hash(key) == hash(
        PositionKey(
            venue="bitget", instrument_id="BTC/USDT", strategy_id="strat-1", execution_id="exec-1"
        )
    )
