"""RD-20 — Pure rules for normalizing OpenDART filings into `CorporateAction`.

Spec: docs/specs/L4_research_data_and_market_ecosystem_v1.0.md §9 RD-20,
ADR-2026-09-06-H D3 (domestic corporate actions are sourced from the
electronic disclosure originals, not from a vendor).

**Unverified**: `OpenDartFiling` is not a direct mapping of the JSON field
names actually returned by the FSS OpenDART Open API — the conversion from
that raw source (e.g. the actual field names of each dividend-decision,
stock-split-decision, and merger-decision API) into this stable internal
representation must be separately verified in `adapters/opendart/` when
the real API integration is built. This module only deals with the
representation after that conversion, and also receives input where
instrument identification (`instrument_id`) has already been resolved
(symbol -> instrument_id lookup is I/O, so it happens outside pure
functions).

A correcting filing (`corrects_rcept_no` populated) creates a new
`CorporateAction` with the same `(instrument_id, action_type, ex_date)` as
the original but a different `known_at` (receipt time) — it never modifies
the existing row. Append-only storage (no UPDATE) is guaranteed by
`adapters/opendart/postgres_filing_repository.py`; this function only
purely computes the values to be passed to that storage.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Literal
from uuid import UUID

from src.foundation.market_data.contracts.v1 import CorporateAction

__all__ = ["FilingParseError", "OpenDartFiling", "normalize_filing"]

_REPORT_TYPES = ("SPLIT", "CASH_DIVIDEND", "MERGER")


@dataclass(frozen=True, slots=True)
class OpenDartFiling:
    """The minimal normalization input extracted from one OpenDART filing.

    `rcept_no` (the DART receipt number) uniquely identifies a single
    filing document, so it is used both as `CorporateAction.source_ref`
    and as the store's idempotency key — a correcting filing has its own
    `rcept_no` distinct from the original filing's, so applying a
    correction adds a new `rcept_no` row instead of overwriting the
    original row (which is why a new row naturally results, rather than an
    UPDATE).
    """

    instrument_id: UUID
    rcept_no: str
    report_type: Literal["SPLIT", "CASH_DIVIDEND", "MERGER"]
    event_date: date
    known_at: datetime
    corrects_rcept_no: str | None = None
    split_ratio_before: Decimal | None = None
    split_ratio_after: Decimal | None = None
    dividend_per_share: Decimal | None = None
    merger_ratio: Decimal | None = None


class FilingParseError(ValueError):
    """The filing cannot be normalized — instead of silently discarding it,
    this is raised as an exception so the caller can leave it in an
    unprocessed queue (RD-20 DoD)."""

    def __init__(self, filing: OpenDartFiling, reason: str) -> None:
        super().__init__(
            f"rcept_no={filing.rcept_no} report_type={filing.report_type}: {reason}"
        )
        self.filing = filing
        self.reason = reason


def _require_positive(filing: OpenDartFiling, value: Decimal | None, field: str) -> Decimal:
    if value is None:
        raise FilingParseError(filing, f"{field} 없음")
    if value <= 0:
        raise FilingParseError(filing, f"{field}는 양수여야 함(받은 값: {value})")
    return value


def _split_ratio(filing: OpenDartFiling) -> Decimal:
    before = _require_positive(filing, filing.split_ratio_before, "split_ratio_before")
    after = _require_positive(filing, filing.split_ratio_after, "split_ratio_after")
    return before / after


def normalize_filing(filing: OpenDartFiling) -> CorporateAction:
    """One filing -> one `CorporateAction`. Failure always raises `FilingParseError`."""
    if filing.known_at.tzinfo is None:
        raise FilingParseError(filing, "known_at는 tz-aware여야 함")
    if not filing.rcept_no:
        raise FilingParseError(filing, "rcept_no 없음")

    if filing.report_type == "SPLIT":
        ratio = _split_ratio(filing)
        cash_amount = None
    elif filing.report_type == "CASH_DIVIDEND":
        ratio = Decimal(1)
        cash_amount = _require_positive(filing, filing.dividend_per_share, "dividend_per_share")
    elif filing.report_type == "MERGER":
        ratio = _require_positive(filing, filing.merger_ratio, "merger_ratio")
        cash_amount = None
    else:  # pragma: no cover - Literal prevents this, but this is the fail-closed default
        raise FilingParseError(filing, f"알 수 없는 report_type: {filing.report_type}")

    return CorporateAction(
        action_type=filing.report_type,
        instrument_id=filing.instrument_id,
        ex_date=filing.event_date,
        ratio=ratio,
        cash_amount=cash_amount,
        source_ref=filing.rcept_no,
        known_at=filing.known_at,
    )
