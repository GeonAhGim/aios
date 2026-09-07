"""FA-15 -- deterministic replay verification: byte-identical state comparison.

Spec: docs/specs/L4_ibor_fund_accounting_and_resilience_v1.0.md#9 FA-15
(depends on FA-14=task-2050 -- "1-day replay must be byte-identical to the
current table").

This module owns only the pure, I/O-free half of FA-15: normalizing a
(replayed, actual) state pair into a deterministic byte digest and folding
many such pairs (one per stream -- an order_id, a ledger account_code, ...)
into a single report. It does not query Postgres and does not call FA-14's
projections/*.py itself -- scripts/replay_verify.py is the adapter that
loads event timelines, calls FA-14's projection functions (reused, not
re-implemented -- a second, diverging set of folding rules would make
"byte-identical" prove nothing), and hands the (replayed, actual) pairs in
here.

Determinism: `digest_state` never reads the clock and never depends on
dict/set iteration order -- every value is normalized (`_to_jsonable`)
before `canonical_json`(LC-3 reuse) sorts its keys, and `verify_replay`
iterates `sorted(streams)` rather than `streams.items()` so `combined_digest`
is independent of the order asyncpg happened to return rows in. Same input
-> same digest, every time.
"""
from __future__ import annotations

import hashlib
from collections.abc import Mapping
from dataclasses import asdict, dataclass, is_dataclass
from decimal import Decimal
from typing import Any

from src.foundation.ledger.domain.hash_chain import canonical_json

StreamKey = tuple[str, str]
"""`(domain, key)` -- e.g. `("orders", str(order_id))` or `("ledger", account_code)`."""


def _decimal_to_str(value: Decimal) -> str:
    """Value-equal `Decimal`s must digest the same regardless of scale --
    `orders.filled_quantity` is `NUMERIC(30,10)` (`Decimal("10.0000000000")`
    on read) while the replayed value folded in Python from `fills` lands on
    `Decimal("10")` (fewer trailing zeros); both represent the same amount
    and must not register as a false mismatch. `normalize()` collapses
    trailing zeros (at the cost of scientific notation for round numbers,
    e.g. `Decimal("1E+1")`), and `format(..., "f")` converts that back to
    plain fixed-point. Zero gets its sign pinned positive -- `Decimal("-0.00")
    .normalize()` is `Decimal("-0")`, which would otherwise digest
    differently from `Decimal("0")` despite being numerically equal.
    """
    normalized = value.normalize()
    if normalized == 0:
        normalized = Decimal(0)
    return format(normalized, "f")


def _to_jsonable(value: Any) -> Any:
    """Normalize `value` into a structure `canonical_json` serializes
    deterministically. Dataclasses become plain dicts (field order does not
    matter -- `canonical_json` sorts keys); `Decimal` is normalized to a
    scale-independent string (`_decimal_to_str`); everything else is passed
    through to `json.dumps(..., default=str)`.
    """
    if is_dataclass(value) and not isinstance(value, type):
        return {k: _to_jsonable(v) for k, v in asdict(value).items()}
    if isinstance(value, Mapping):
        return {str(k): _to_jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_to_jsonable(v) for v in value]
    if isinstance(value, Decimal):
        return _decimal_to_str(value)
    return value


def digest_state(value: Any) -> str:
    """sha256 of `value`'s canonical byte serialization."""
    return hashlib.sha256(canonical_json(_to_jsonable(value)).encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class StreamDiff:
    """One stream whose replayed-state digest does not match its current
    table row's digest -- the concrete evidence a mismatch report carries."""

    domain: str
    key: str
    replayed_digest: str
    actual_digest: str


@dataclass(frozen=True, slots=True)
class ReplayReport:
    """Result of `verify_replay` over every stream touched in the window."""

    streams_checked: int
    mismatches: tuple[StreamDiff, ...]
    combined_digest: str

    @property
    def ok(self) -> bool:
        return not self.mismatches


def verify_replay(streams: Mapping[StreamKey, tuple[Any, Any]]) -> ReplayReport:
    """Compare replayed vs. actual state for every `(domain, key)` stream.

    `streams` maps each touched stream to a `(replayed, actual)` pair -- the
    caller has already folded `replayed` from the stream's full event
    history via FA-14's projections/*.py and read `actual` from the stream's
    current table row. Missing/`None` values on either side still digest (to
    a fixed "empty" digest) rather than being skipped, so a stream that
    disappeared from one side is a mismatch, not a silent pass -- fail
    closed, matching FA-14's dropped-event negative tests.
    """
    mismatches: list[StreamDiff] = []
    digests: list[str] = []
    for domain, key in sorted(streams):
        replayed, actual = streams[(domain, key)]
        replayed_digest = digest_state(replayed)
        actual_digest = digest_state(actual)
        digests.append(f"{domain}:{key}:{replayed_digest}:{actual_digest}")
        if replayed_digest != actual_digest:
            mismatches.append(
                StreamDiff(
                    domain=domain,
                    key=key,
                    replayed_digest=replayed_digest,
                    actual_digest=actual_digest,
                )
            )
    combined_digest = hashlib.sha256("|".join(digests).encode("utf-8")).hexdigest()
    return ReplayReport(
        streams_checked=len(streams),
        mismatches=tuple(mismatches),
        combined_digest=combined_digest,
    )
