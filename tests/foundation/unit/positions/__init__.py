"""Focused regression tests for the positions package invariants."""

from __future__ import annotations

import time
from uuid import UUID

import pytest

from src.foundation.positions.domain import position_key

_VALID_KEY = "bitget:BTC/USDT:strategy-1:execution-1:11111111-1111-1111-1111-111111111111"


@pytest.mark.parametrize(
    "raw",
    [
        "bitget:BTC/USDT:strategy-1:execution-1",
        f"{_VALID_KEY}:extra",
        "",
    ],
)
def test_position_key_rejects_wrong_part_count(raw: str) -> None:
    """A position identifier must remain five-part and fail closed."""
    with pytest.raises(position_key.InvalidPositionKeyError):
        position_key.PositionKey.parse(raw)


def test_position_key_rejects_empty_component() -> None:
    """Empty identity components cannot enter journal or snapshot keys."""
    with pytest.raises(position_key.InvalidPositionKeyError):
        position_key.PositionKey.parse(
            "bitget::strategy-1:execution-1:11111111-1111-1111-1111-111111111111"
        )


def test_position_key_rejects_non_uuid_portfolio_id() -> None:
    """The portfolio component must be a UUID, never an opaque fallback."""
    with pytest.raises(position_key.InvalidPositionKeyError):
        position_key.PositionKey.parse(
            "bitget:BTC/USDT:strategy-1:execution-1:not-a-uuid"
        )


def test_position_key_propagates_uuid_dependency_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """UUID conversion failures are not silently converted into valid keys."""

    def _raise_dependency_error(_: str) -> UUID:
        raise RuntimeError("UUID dependency unavailable")

    monkeypatch.setattr(position_key, "UUID", _raise_dependency_error)

    with pytest.raises(RuntimeError, match="UUID dependency unavailable"):
        position_key.PositionKey.parse(_VALID_KEY)


@pytest.mark.perf
def test_position_key_parse_throughput_stays_within_budget() -> None:
    """Central parsing must stay below the pure-domain hot-path budget."""
    iterations = 10_000
    started = time.perf_counter()
    for _ in range(iterations):
        assert str(position_key.PositionKey.parse(_VALID_KEY)) == _VALID_KEY
    elapsed_ms = (time.perf_counter() - started) * 1000

    assert elapsed_ms < 1_000, (
        f"PositionKey parse/serialize exceeded 1000ms budget: {elapsed_ms:.1f}ms"
    )
