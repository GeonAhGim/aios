"""L4_compliance_and_regulatory_v1.0.md#9 CM-15 — normalize one executed
fill plus its compliance decision into a jurisdiction-neutral trade report
record, then project that record onto one domestic report layout.

Persistence/immutable storage belongs to CM-16
(`reporting/ports/report_submitter.py` + `application/generate_report.py`);
this module is pure — no I/O, no clock, no randomness. `report_hash` is the
determinism anchor CM-16 will rely on to prove "regenerate from the same
inputs -> identical bytes" before writing a WORM row: it reuses R-01's
`canonical_json`/`sha256_hex` (`src.core.risk.hashing`) the same way CM-3's
`rule_bundle.bundle_hash()` does, so it never reimplements hashing.

Domestic field mapping (UNVERIFIED): the exact domestic trade-report schema
(field codes, column order, code-value sets a Korean financial industry
association or exchange would require) has not been checked against a
published regulatory form. `to_domestic_report_fields()` only fixes the
*shape* — Korean field labels over `TradeReportRecord`'s common fields — so
an MVP-2 submission adapter has one canonical place to bolt an exact
codeset onto; this is an output artifact, not a submittable filing yet.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from uuid import UUID

from src.core.risk.hashing import canonical_json, sha256_hex
from src.data.models.trading import OrderSide
from src.foundation.mandates.contracts.v1 import ComplianceDecision


class TradeReportInputError(ValueError):
    """Required or malformed field for trade report normalization.

    Fail-closed (I-02): missing evidence never gets silently defaulted
    into a reportable-looking record.
    """


@dataclass(frozen=True)
class TradeReportRecord:
    """Common fields any jurisdiction's trade report needs, before that
    jurisdiction's own field names/codes are applied. `report_hash` covers
    every other field, so two normalizations of the same fill always
    produce the same hash regardless of process/interpreter."""

    order_id: UUID
    trade_id: UUID
    compliance_decision_id: UUID
    instrument: str
    venue: str
    side: OrderSide
    quantity: Decimal
    price: Decimal
    currency: str
    executed_at: datetime
    trader_id: str
    report_hash: str


def _positive_decimal(value: Decimal, field: str) -> None:
    if not isinstance(value, Decimal) or not value.is_finite() or value <= 0:
        raise TradeReportInputError(f"{field} must be a finite positive Decimal")


def _nonblank(value: str, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise TradeReportInputError(f"{field} must be a non-blank string")
    return value


def normalize_trade_report(
    *,
    order_id: UUID,
    trade_id: UUID,
    compliance_decision: ComplianceDecision,
    instrument: str,
    venue: str,
    side: OrderSide,
    quantity: Decimal,
    price: Decimal,
    currency: str,
    executed_at: datetime,
    trader_id: str,
) -> TradeReportRecord:
    """Normalize one executed fill into a `TradeReportRecord`.

    Requires the fill's `ComplianceDecision` (CM-4's WORM `policy_decision`
    projection, CM-1's `compliance_decision_from_policy_decision`) so a
    trade report can never be produced for a fill with no recorded
    compliance judgment — even a `DENY` decision still normalizes (a
    post-trade audit needs to see what should have been blocked); only a
    *missing* decision is rejected.
    """
    if not isinstance(compliance_decision, ComplianceDecision):
        raise TradeReportInputError("compliance_decision is required")
    if not isinstance(side, OrderSide):
        raise TradeReportInputError("side must be an OrderSide")
    _positive_decimal(quantity, "quantity")
    _positive_decimal(price, "price")
    _nonblank(instrument, "instrument")
    _nonblank(venue, "venue")
    _nonblank(currency, "currency")
    _nonblank(trader_id, "trader_id")
    if executed_at.tzinfo is None:
        raise TradeReportInputError("executed_at must be tz-aware")

    payload = {
        "order_id": order_id,
        "trade_id": trade_id,
        "compliance_decision_id": compliance_decision.decision_id,
        "instrument": instrument,
        "venue": venue,
        "side": side.value,
        "quantity": quantity,
        "price": price,
        "currency": currency,
        "executed_at": executed_at,
        "trader_id": trader_id,
    }
    report_hash = sha256_hex(canonical_json(payload))
    return TradeReportRecord(
        order_id=order_id,
        trade_id=trade_id,
        compliance_decision_id=compliance_decision.decision_id,
        instrument=instrument,
        venue=venue,
        side=side,
        quantity=quantity,
        price=price,
        currency=currency,
        executed_at=executed_at,
        trader_id=trader_id,
        report_hash=report_hash,
    )


def to_domestic_report_fields(record: TradeReportRecord) -> dict[str, str]:
    """Project a `TradeReportRecord` onto one domestic (KRX-style) report
    layout — see module docstring UNVERIFIED note on the exact codeset."""
    return {
        "종목코드": record.instrument,
        "거래소": record.venue,
        "매매구분": "매수" if record.side == OrderSide.BUY else "매도",
        "수량": str(record.quantity),
        "단가": str(record.price),
        "통화": record.currency,
        "체결시각": record.executed_at.isoformat(),
        "주문번호": str(record.order_id),
        "체결번호": str(record.trade_id),
        "판정ID": str(record.compliance_decision_id),
        "보고서해시": record.report_hash,
    }
