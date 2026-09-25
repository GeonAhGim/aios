"""L4_compliance_and_regulatory_v1.0.md#9 CM-10 -- `domain/market_abuse.py`.

Post-trade surveillance: three market-abuse patterns detected *after* the
fact from a fixed window of fills/orders, never in the order path. `detect`
is the single entry point and produces suspicion hits only -- it never
blocks anything (that wiring is CM-11's `application/evaluate_post_trade.py`,
not this leaf).

Distinct from CM-9's `domain/rules/wash_trade.py`: that rule denies a *new*
order pre-trade by checking it against currently *open* opposing orders.
Pattern 1 here looks backward at already-*filled* executions and shares no
import, type, or judging logic with that module (see the static test in
`tests/unit/foundation/mandates/test_market_abuse.py`).

Pure and deterministic (CM-A2): no clock, RNG, or I/O of any kind -- every
timestamp is supplied by the caller inside `window`.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Any

from src.foundation.mandates.contracts.v1 import ComplianceVerdict

PATTERN_WASH_TRADE = "market_abuse.wash_trade"
PATTERN_MARKING_THE_CLOSE = "market_abuse.marking_the_close"
PATTERN_SPOOFING = "market_abuse.spoofing"

REASON_DATA_MISSING = "DATA_MISSING"

_DEFAULT_WASH_WINDOW_SEC = 60
_DEFAULT_CLOSING_WINDOW_SEC = 600
_DEFAULT_CLOSING_SHARE_PCT = 30
_DEFAULT_SPOOF_CANCEL_SEC = 5
_DEFAULT_SPOOF_MIN_ORDERS = 3
_DEFAULT_SPOOF_RATIO = 10


@dataclass(frozen=True)
class AbuseHit:
    """One suspected-abuse finding. Mirrors `contracts.v1.RuleHit`'s shape
    (severity/message/evidence) but is its own type: a post-trade suspicion
    is not a pre-trade rule verdict, and §9 CM-10 forbids minting a new
    severity enum for it -- `severity` reuses `ComplianceVerdict`.
    """

    pattern_id: str
    severity: ComplianceVerdict
    reason_code: str
    message: str
    evidence: Mapping[str, Any] = field(default_factory=dict)


def _missing(pattern_id: str, field_name: str) -> AbuseHit:
    return AbuseHit(
        pattern_id=pattern_id,
        severity=ComplianceVerdict.DENY,
        reason_code=REASON_DATA_MISSING,
        message=f"required field '{field_name}' missing from detection window",
        evidence={"missing_field": field_name},
    )


def _safe(fn: Any, pattern_id: str, *args: Any) -> list[AbuseHit]:
    """I-02 fail-closed: a field missing deeper than the top level of
    `window` (e.g. one fill dict lacking `owner_id`) must still surface as a
    `DATA_MISSING` hit instead of a silently-empty result or an uncaught
    crash -- same posture as `domain/evaluator.py::_run_rule`. `ArithmeticError`
    is caught alongside `KeyError`/`TypeError` because a corrupted numeric or
    datetime value can pass every `isinstance` guard upstream and still raise
    it deep in `qty`/`executed_at` arithmetic (`Decimal.__add__`,
    `datetime.__sub__`); letting that escape here would turn a data-integrity
    problem into a silent gap instead of a `DATA_MISSING` hit (DEEPEN
    task-2864, mirrors the CM-6/CM-7 corrupted-numeric-type findings)."""
    try:
        return list(fn(*args))
    except (KeyError, TypeError, ArithmeticError) as exc:
        return [_missing(pattern_id, str(exc))]


def detect(window: Mapping[str, Any], params: Mapping[str, Any]) -> list[AbuseHit]:
    """§9 CM-10 public contract. Never returns an empty list to mean
    "could not judge" -- missing required data always yields an explicit
    `DATA_MISSING` `AbuseHit` per affected pattern (I-02)."""
    try:
        fills = window["fills"]
    except KeyError:
        return [
            _missing(pattern_id, "fills")
            for pattern_id in (PATTERN_WASH_TRADE, PATTERN_MARKING_THE_CLOSE, PATTERN_SPOOFING)
        ]

    hits: list[AbuseHit] = []
    hits.extend(_safe(_detect_wash_trade, PATTERN_WASH_TRADE, fills, params))

    try:
        market_close_at = window["market_close_at"]
    except KeyError:
        hits.append(_missing(PATTERN_MARKING_THE_CLOSE, "market_close_at"))
    else:
        hits.extend(
            _safe(
                _detect_marking_the_close,
                PATTERN_MARKING_THE_CLOSE,
                fills,
                market_close_at,
                params,
            )
        )

    try:
        orders = window["orders"]
    except KeyError:
        hits.append(_missing(PATTERN_SPOOFING, "orders"))
    else:
        hits.extend(_safe(_detect_spoofing, PATTERN_SPOOFING, orders, fills, params))

    return hits


def _detect_wash_trade(
    fills: Sequence[Mapping[str, Any]], params: Mapping[str, Any]
) -> list[AbuseHit]:
    """Pattern 1: same tenant + instrument, opposite-side fills owned by the
    same beneficial owner, closing within `wash_window_sec` of each other."""
    window_sec = params.get("wash_window_sec", _DEFAULT_WASH_WINDOW_SEC)
    hits: list[AbuseHit] = []
    for i in range(len(fills)):
        a = fills[i]
        for j in range(i + 1, len(fills)):
            b = fills[j]
            if a["tenant_id"] != b["tenant_id"]:
                continue
            if a["instrument_id"] != b["instrument_id"]:
                continue
            if a["owner_id"] != b["owner_id"]:
                continue
            if a["side"] == b["side"]:
                continue
            delta_sec = abs((a["executed_at"] - b["executed_at"]).total_seconds())
            if delta_sec <= window_sec:
                hits.append(
                    AbuseHit(
                        pattern_id=PATTERN_WASH_TRADE,
                        severity=ComplianceVerdict.WARN,
                        reason_code="WASH_TRADE_MATCH",
                        message=(
                            f"opposite-side fills {a['fill_id']!r}/{b['fill_id']!r} by the same "
                            f"owner {delta_sec:.0f}s apart"
                        ),
                        evidence={
                            "tenant_id": a["tenant_id"],
                            "instrument_id": a["instrument_id"],
                            "owner_id": a["owner_id"],
                            "fill_ids": [a["fill_id"], b["fill_id"]],
                            "delta_sec": delta_sec,
                        },
                    )
                )
    return hits


def _in_closing_window(ts: datetime, market_close_at: datetime, window_sec: int) -> bool:
    delta_sec = (market_close_at - ts).total_seconds()
    return 0 <= delta_sec <= window_sec


def _detect_marking_the_close(
    fills: Sequence[Mapping[str, Any]],
    market_close_at: datetime,
    params: Mapping[str, Any],
) -> list[AbuseHit]:
    """Pattern 2: one tenant's own fills inside `closing_window_sec` before
    the close dominate that instrument's total traded volume in the same
    window. Fills outside the window count toward neither side of the ratio."""
    window_sec = params.get("closing_window_sec", _DEFAULT_CLOSING_WINDOW_SEC)
    share_pct_limit = Decimal(str(params.get("closing_share_pct", _DEFAULT_CLOSING_SHARE_PCT)))

    total_by_instrument: dict[Any, Decimal] = defaultdict(lambda: Decimal(0))
    self_by_key: dict[tuple[Any, Any], Decimal] = defaultdict(lambda: Decimal(0))
    for f in fills:
        if not _in_closing_window(f["executed_at"], market_close_at, window_sec):
            continue
        total_by_instrument[f["instrument_id"]] += f["qty"]
        self_by_key[(f["tenant_id"], f["instrument_id"])] += f["qty"]

    hits: list[AbuseHit] = []
    for (tenant_id, instrument_id), self_qty in self_by_key.items():
        total_qty = total_by_instrument[instrument_id]
        if total_qty == 0:
            continue
        share_pct = (self_qty / total_qty) * Decimal(100)
        if share_pct > share_pct_limit:
            hits.append(
                AbuseHit(
                    pattern_id=PATTERN_MARKING_THE_CLOSE,
                    severity=ComplianceVerdict.WARN,
                    reason_code="CLOSING_SHARE_EXCEEDED",
                    message=(
                        f"tenant {tenant_id!r} closing-window share {share_pct}% of "
                        f"instrument {instrument_id!r} exceeds limit {share_pct_limit}%"
                    ),
                    evidence={
                        "tenant_id": tenant_id,
                        "instrument_id": instrument_id,
                        "self_qty": str(self_qty),
                        "total_qty": str(total_qty),
                        "share_pct": str(share_pct),
                        "limit_pct": str(share_pct_limit),
                    },
                )
            )
    return hits


def _detect_spoofing(
    orders: Sequence[Mapping[str, Any]],
    fills: Sequence[Mapping[str, Any]],
    params: Mapping[str, Any],
) -> list[AbuseHit]:
    """Pattern 3: 3+ unfilled orders per tenant/instrument cancelled within
    `spoof_cancel_sec` of submission, whose combined quantity exceeds
    `spoof_ratio`x that tenant/instrument's own filled quantity in the window."""
    cancel_sec = params.get("spoof_cancel_sec", _DEFAULT_SPOOF_CANCEL_SEC)
    min_orders = params.get("spoof_min_orders", _DEFAULT_SPOOF_MIN_ORDERS)
    ratio = Decimal(str(params.get("spoof_ratio", _DEFAULT_SPOOF_RATIO)))

    cancel_qty_by_key: dict[tuple[Any, Any], Decimal] = defaultdict(lambda: Decimal(0))
    cancel_count_by_key: dict[tuple[Any, Any], int] = defaultdict(int)
    for o in orders:
        canceled_at = o["canceled_at"]
        if canceled_at is None:
            continue
        delta_sec = (canceled_at - o["submitted_at"]).total_seconds()
        if delta_sec <= cancel_sec:
            key = (o["tenant_id"], o["instrument_id"])
            cancel_qty_by_key[key] += o["qty"]
            cancel_count_by_key[key] += 1

    self_fill_qty_by_key: dict[tuple[Any, Any], Decimal] = defaultdict(lambda: Decimal(0))
    for f in fills:
        self_fill_qty_by_key[(f["tenant_id"], f["instrument_id"])] += f["qty"]

    hits: list[AbuseHit] = []
    for key, count in cancel_count_by_key.items():
        if count < min_orders:
            continue
        cancel_qty = cancel_qty_by_key[key]
        fill_qty = self_fill_qty_by_key[key]
        if fill_qty == 0:
            continue
        if cancel_qty > ratio * fill_qty:
            tenant_id, instrument_id = key
            hits.append(
                AbuseHit(
                    pattern_id=PATTERN_SPOOFING,
                    severity=ComplianceVerdict.WARN,
                    reason_code="SPOOF_CANCEL_RATIO_EXCEEDED",
                    message=(
                        f"tenant {tenant_id!r} instrument {instrument_id!r}: {count} "
                        f"quick-cancelled orders totalling {cancel_qty} exceed "
                        f"{ratio}x self-fill qty {fill_qty}"
                    ),
                    evidence={
                        "tenant_id": tenant_id,
                        "instrument_id": instrument_id,
                        "cancel_count": count,
                        "cancel_qty": str(cancel_qty),
                        "self_fill_qty": str(fill_qty),
                        "ratio_limit": str(ratio),
                    },
                )
            )
    return hits
