"""Backtest domain/universe.py 단위테스트 -- DB 없이 순수 함수만 검증.

Spec: docs/specs/L4_strategy_portfolio_backtest_v1.0.md#§9 L29 DoD.
"""
from datetime import datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from src.foundation.backtest.domain.universe import (
    SurvivorshipUnknownError,
    UniverseMember,
    UniverseSnapshot,
    compute_snapshot_hash,
    is_member,
)

_UTC = timezone.utc
_AS_OF = datetime(2026, 3, 1, tzinfo=_UTC)
_LISTED_FROM = datetime(2026, 1, 1, tzinfo=_UTC)
_DELISTED_AT = datetime(2026, 2, 15, tzinfo=_UTC)


def _snapshot(members: list[UniverseMember], *, as_of: datetime = _AS_OF) -> UniverseSnapshot:
    return UniverseSnapshot(
        as_of=as_of, members=members, snapshot_hash=compute_snapshot_hash(as_of, members)
    )


def _member(symbol: str = "ABC", delisted_at: datetime | None = _DELISTED_AT) -> UniverseMember:
    return UniverseMember(symbol=symbol, listed_from=_LISTED_FROM, delisted_at=delisted_at)


# -- (a) naive datetime rejection --------------------------------------------


def test_member_rejects_naive_listed_from() -> None:
    with pytest.raises(ValidationError):
        UniverseMember(symbol="ABC", listed_from=datetime(2026, 1, 1), delisted_at=None)


def test_member_rejects_naive_delisted_at() -> None:
    with pytest.raises(ValidationError):
        UniverseMember(
            symbol="ABC", listed_from=_LISTED_FROM, delisted_at=datetime(2026, 2, 15)
        )


def test_snapshot_rejects_naive_as_of() -> None:
    with pytest.raises(ValidationError):
        UniverseSnapshot(as_of=datetime(2026, 3, 1), members=[], snapshot_hash="x")


def test_is_member_rejects_naive_at() -> None:
    snap = _snapshot([_member()])
    with pytest.raises(ValueError, match="tz-aware"):
        is_member(snap, "ABC", datetime(2026, 1, 15))


# -- (b) boundary correctness (5 cases) --------------------------------------


def test_is_member_before_listing_is_false() -> None:
    snap = _snapshot([_member()])
    assert is_member(snap, "ABC", datetime(2025, 12, 31, 23, 59, tzinfo=_UTC)) is False


def test_is_member_on_listing_instant_is_true_inclusive() -> None:
    snap = _snapshot([_member()])
    assert is_member(snap, "ABC", _LISTED_FROM) is True


def test_is_member_just_before_delisting_is_true() -> None:
    snap = _snapshot([_member()])
    assert is_member(snap, "ABC", datetime(2026, 2, 14, 23, 59, tzinfo=_UTC)) is True


def test_is_member_on_delisting_instant_is_false_exclusive() -> None:
    snap = _snapshot([_member()])
    assert is_member(snap, "ABC", _DELISTED_AT) is False


def test_is_member_never_delisted_is_true_far_future() -> None:
    far_future = datetime(2099, 1, 1, tzinfo=_UTC)
    snap = _snapshot([_member(delisted_at=None)], as_of=far_future)
    assert is_member(snap, "ABC", far_future) is True


# -- (c) fail-closed on unknown survivorship ----------------------------------


def test_is_member_unknown_symbol_raises_not_false() -> None:
    snap = _snapshot([_member(symbol="ABC")])
    with pytest.raises(SurvivorshipUnknownError) as exc_info:
        is_member(snap, "ZZZ", _LISTED_FROM)
    assert exc_info.value.error_code == "INTEGRITY_SURVIVORSHIP_UNKNOWN"


def test_is_member_query_after_as_of_raises() -> None:
    snap = _snapshot([_member()])
    with pytest.raises(SurvivorshipUnknownError):
        is_member(snap, "ABC", _AS_OF + timedelta(days=1))


# -- (d) snapshot_hash stability -----------------------------------------------


def test_snapshot_hash_is_order_independent() -> None:
    m1 = _member(symbol="AAA")
    m2 = _member(symbol="BBB")
    hash_forward = compute_snapshot_hash(_AS_OF, [m1, m2])
    hash_reversed = compute_snapshot_hash(_AS_OF, [m2, m1])
    assert hash_forward == hash_reversed


def test_snapshot_hash_changes_when_delisted_at_shifts_by_one_day() -> None:
    original = _member(symbol="AAA", delisted_at=_DELISTED_AT)
    shifted = _member(symbol="AAA", delisted_at=_DELISTED_AT + timedelta(days=1))
    hash_original = compute_snapshot_hash(_AS_OF, [original])
    hash_shifted = compute_snapshot_hash(_AS_OF, [shifted])
    assert hash_original != hash_shifted
