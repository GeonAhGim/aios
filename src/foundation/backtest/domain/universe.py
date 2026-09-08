"""L29 -- survivorship universe: per-symbol listed/delisted windows.

Spec: docs/specs/L4_strategy_portfolio_backtest_v1.0.md#§9 L29 (public
contract in §2's domain/universe.py row).

A symbol's survivorship status is only knowable as of the snapshot that
recorded it (109번 §5, I-03) -- so `is_member` never guesses. A symbol the
snapshot never heard of, or a query for a time after the snapshot's `as_of`,
is not "not a member" -- it is unknown, and unknown must fail closed
(`SurvivorshipUnknownError`) rather than silently collapse to `False`. Only
a symbol the snapshot does know about, queried at or before `as_of`, can
return a plain `True`/`False` for whether it was listed at that instant.
"""
from __future__ import annotations

import hashlib
from datetime import datetime
from typing import ClassVar

from pydantic import BaseModel, field_validator

__all__ = [
    "SurvivorshipUnknownError",
    "UniverseMember",
    "UniverseSnapshot",
    "compute_snapshot_hash",
    "is_member",
]


def _require_tz_aware(value: datetime, *, name: str) -> datetime:
    if value.tzinfo is None:
        raise ValueError(f"{name} must be tz-aware (naive datetime rejected)")
    return value


class UniverseMember(BaseModel):
    """One symbol's listed window. `delisted_at=None` means still listed as
    of the snapshot's `as_of` -- there is no "far future" sentinel value."""

    symbol: str
    listed_from: datetime
    delisted_at: datetime | None = None

    @field_validator("listed_from")
    @classmethod
    def _listed_from_tz_aware(cls, v: datetime) -> datetime:
        return _require_tz_aware(v, name="listed_from")

    @field_validator("delisted_at")
    @classmethod
    def _delisted_at_tz_aware(cls, v: datetime | None) -> datetime | None:
        if v is None:
            return v
        return _require_tz_aware(v, name="delisted_at")


def compute_snapshot_hash(as_of: datetime, members: list[UniverseMember]) -> str:
    """Order-independent: members are sorted by symbol before hashing, so a
    snapshot built from the same members in a different order hashes the
    same. Any change to a member's window (including `delisted_at` by a
    single day) changes the hash."""
    _require_tz_aware(as_of, name="as_of")
    ordered = sorted(members, key=lambda m: m.symbol)
    lines = [as_of.isoformat()]
    for m in ordered:
        delisted = m.delisted_at.isoformat() if m.delisted_at is not None else ""
        lines.append(f"{m.symbol}|{m.listed_from.isoformat()}|{delisted}")
    canonical = "\n".join(lines)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class UniverseSnapshot(BaseModel):
    as_of: datetime
    members: list[UniverseMember]
    snapshot_hash: str

    @field_validator("as_of")
    @classmethod
    def _as_of_tz_aware(cls, v: datetime) -> datetime:
        return _require_tz_aware(v, name="as_of")


class SurvivorshipUnknownError(ValueError):
    """`INTEGRITY_SURVIVORSHIP_UNKNOWN` -- raised instead of returning
    `False` whenever membership cannot be determined from this snapshot:
    the symbol is absent from `members`, or `at` is after the snapshot's
    `as_of` (a snapshot only carries as-of-that-instant knowledge)."""

    error_code: ClassVar[str] = "INTEGRITY_SURVIVORSHIP_UNKNOWN"

    def __init__(self, *, symbol: str, at: datetime) -> None:
        self.symbol = symbol
        self.at = at
        super().__init__(
            f"{self.error_code}: symbol={symbol!r} at={at.isoformat()} -- "
            "survivorship for this symbol/time is not known from this snapshot"
        )


def is_member(u: UniverseSnapshot, symbol: str, at: datetime) -> bool:
    _require_tz_aware(at, name="at")
    if at > u.as_of:
        raise SurvivorshipUnknownError(symbol=symbol, at=at)
    member = next((m for m in u.members if m.symbol == symbol), None)
    if member is None:
        raise SurvivorshipUnknownError(symbol=symbol, at=at)
    if at < member.listed_from:
        return False
    if member.delisted_at is not None and at >= member.delisted_at:
        return False
    return True
