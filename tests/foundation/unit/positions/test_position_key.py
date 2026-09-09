"""LB-2/FA-0d — position_key 직렬화·파싱 단위 테스트.

Spec: docs/specs/L4_market_data_positions_ledger_v1.0.md#§9 LB-2,
docs/specs/L4_ibor_fund_accounting_and_resilience_v1.0.md#FA-0d
(`venue:instrument_id:strategy_id:execution_id:portfolio_id` 5부분 형식).
"""
from __future__ import annotations

from uuid import UUID, uuid4

import pytest

from src.foundation.positions.domain.position_key import (
    InvalidPositionKeyError,
    PositionKey,
)

_PORTFOLIO_ID = UUID("11111111-1111-1111-1111-111111111111")


def test_str_serializes_five_parts_with_colon_delimiter() -> None:
    key = PositionKey(
        venue="bitget",
        instrument_id="BTC/USDT",
        strategy_id="strat-1",
        execution_id="exec-1",
        portfolio_id=_PORTFOLIO_ID,
    )
    assert str(key) == f"bitget:BTC/USDT:strat-1:exec-1:{_PORTFOLIO_ID}"


def test_parse_round_trips_with_str() -> None:
    raw = f"bitget:BTC/USDT:strat-1:exec-1:{_PORTFOLIO_ID}"
    key = PositionKey.parse(raw)
    assert key == PositionKey(
        venue="bitget", instrument_id="BTC/USDT", strategy_id="strat-1", execution_id="exec-1",
        portfolio_id=_PORTFOLIO_ID,
    )
    assert str(key) == raw


def test_parse_rejects_wrong_field_count() -> None:
    with pytest.raises(InvalidPositionKeyError):
        PositionKey.parse(f"bitget:BTC/USDT:strat-1:{_PORTFOLIO_ID}")

    with pytest.raises(InvalidPositionKeyError):
        PositionKey.parse(f"bitget:BTC/USDT:strat-1:exec-1:{_PORTFOLIO_ID}:extra")


def test_parse_rejects_legacy_four_part_key() -> None:
    """FA-0d 이전 4부분 형식은 더 이상 유효하지 않다 — portfolio_id 없는
    레거시 키가 조용히 통과하면 안 된다(fail-closed)."""
    with pytest.raises(InvalidPositionKeyError):
        PositionKey.parse("bitget:BTC/USDT:strat-1:exec-1")


def test_parse_rejects_non_uuid_portfolio_id() -> None:
    with pytest.raises(InvalidPositionKeyError):
        PositionKey.parse("bitget:BTC/USDT:strat-1:exec-1:not-a-uuid")


def test_parse_rejects_empty_raw_string() -> None:
    with pytest.raises(InvalidPositionKeyError):
        PositionKey.parse("")


def test_construct_rejects_empty_component() -> None:
    with pytest.raises(InvalidPositionKeyError):
        PositionKey(
            venue="", instrument_id="BTC/USDT", strategy_id="strat-1", execution_id="exec-1",
            portfolio_id=_PORTFOLIO_ID,
        )


def test_construct_rejects_component_containing_delimiter() -> None:
    with pytest.raises(InvalidPositionKeyError):
        PositionKey(
            venue="bitget:extra",
            instrument_id="BTC/USDT",
            strategy_id="strat-1",
            execution_id="exec-1",
            portfolio_id=_PORTFOLIO_ID,
        )


def test_construct_rejects_non_uuid_portfolio_id() -> None:
    with pytest.raises(InvalidPositionKeyError):
        PositionKey(
            venue="bitget", instrument_id="BTC/USDT", strategy_id="strat-1", execution_id="exec-1",
            portfolio_id="not-a-uuid",  # type: ignore[arg-type]
        )


def test_position_key_is_frozen_and_hashable() -> None:
    key = PositionKey(
        venue="bitget", instrument_id="BTC/USDT", strategy_id="strat-1", execution_id="exec-1",
        portfolio_id=_PORTFOLIO_ID,
    )
    with pytest.raises(AttributeError):
        key.venue = "kis"  # type: ignore[misc]
    assert hash(key) == hash(
        PositionKey(
            venue="bitget", instrument_id="BTC/USDT", strategy_id="strat-1", execution_id="exec-1",
            portfolio_id=_PORTFOLIO_ID,
        )
    )


def test_different_portfolio_id_yields_different_key() -> None:
    """FA-0d의 핵심 불변: venue/instrument/strategy/execution이 같아도
    portfolio_id가 다르면 서로 다른 포지션이다."""
    key_a = PositionKey(
        venue="bitget", instrument_id="BTC/USDT", strategy_id="strat-1", execution_id="exec-1",
        portfolio_id=_PORTFOLIO_ID,
    )
    key_b = PositionKey(
        venue="bitget", instrument_id="BTC/USDT", strategy_id="strat-1", execution_id="exec-1",
        portfolio_id=uuid4(),
    )
    assert key_a != key_b
    assert str(key_a) != str(key_b)
