"""LB-19 positions read API response schema — HTTP details only; the contract
itself wraps `src/foundation/positions/contracts/v1.py` verbatim (Rule 106 §2,
LB-17 principle: "no separate view model"). No request body schema — write
endpoints do not exist.

The journal cursor is an opaque string whose content is the last `sequence_no`
(§4.3: the journal is monotonically increasing on `(position_key, sequence_no)`,
so this single value determines the resume point).
Decode failure is a transport-layer error, not a domain error, so we express it
here as `InvalidCursorError` and let the global handler translate it into a
VALIDATION_INVALID_FIELD envelope."""
from __future__ import annotations

from datetime import date
from uuid import UUID

from pydantic import BaseModel

from src.foundation.positions.contracts.v1 import (
    NAVSnapshot,
    PositionJournalEntryView,
    PositionSnapshotView,
)

__all__ = [
    "InvalidCursorError",
    "NavSeriesResponse",
    "PositionJournalResponse",
    "PositionListResponse",
    "decode_cursor",
    "encode_cursor",
]


class InvalidCursorError(ValueError):
    """The `cursor` query parameter is not in the format issued by this API."""


def encode_cursor(sequence_no: int) -> str:
    return str(sequence_no)


def decode_cursor(raw: str | None) -> int:
    """Return 0 if absent (start from beginning). Reject negatives and non-integers."""
    if raw is None or raw == "":
        return 0
    try:
        value = int(raw)
    except ValueError as exc:
        raise InvalidCursorError(f"cursor 형식이 올바르지 않습니다: {raw!r}") from exc
    if value < 0:
        raise InvalidCursorError(f"cursor는 0 이상이어야 합니다: {raw!r}")
    return value


class PositionListResponse(BaseModel):
    items: list[PositionSnapshotView]


class PositionJournalResponse(BaseModel):
    position_key: str
    items: list[PositionJournalEntryView]


class NavSeriesResponse(BaseModel):
    """`missing_dates` are days within the range where NAV has not yet been
    calculated — we expose the absence directly instead of filling with zeros
    (FD-3.3 "never assume zero")."""

    account_id: UUID
    start_date: date
    end_date: date
    items: list[NAVSnapshot]
    missing_dates: list[date]
