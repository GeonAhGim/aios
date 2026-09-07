"""MP-11 — independent reproduction verification of marketplace listing
performance.

Spec: docs/specs/L4_analytics_authoring_backtest_marketplace_v1.0.md §3.5
(`ReputationScore.reproduced_backtests`), §4.3 (reputation formula consumes
`reproduced_backtests`), ADR-2026-09-06-G §9 MP-11.

The §4.3 reputation formula consumes `reproduced_backtests` as an input, but
until now no leaf turned that value into a "verified reproduction" — if the
platform never actually reruns the Sharpe/DSR/etc. performance claims a
seller posts on a listing, those numbers are nothing but self-reporting
(worse than TradingView's current state — TradingView never makes such
claims in the first place).

This module is pure comparison logic — no I/O. "What hash the seller
claimed" (listing store) and "how the platform reran that backtest"
(`run_backtest`, assumed to already be confirmed as the same artifact/
window/config via BT-9's `reproducibility_key`) are the caller's
responsibility. This module only takes the two results and diffs them
byte-for-byte — the same boundary design as BT-19's `parity_harness.py`
(comparison only; replay is a different leaf).

Fail-closed: if the claimed hash is empty or the reproduced result is empty
(empty `equity_curve`), it rejects immediately with `ValueError` rather than
masquerading as a pass. On mismatch it is flagged `MP_UNVERIFIED_RESULT`,
and `count_verified_backtests()` excludes that entry from the reputation
tally — when in doubt, do not add it.
"""
from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Final

from src.foundation.backtest.domain.models import BacktestResult

MP_UNVERIFIED_RESULT: Final = "MP_UNVERIFIED_RESULT"
RESULT_HASH_SCHEMA: Final = "marketplace-listing-result-hash-1"


def compute_result_hash(result: BacktestResult) -> str:
    """`BacktestResult` (fills/equity curve/metrics) -> canonical sha256 hex
    (64 chars).

    `config` and `warnings` are excluded from the hash because they are
    input/diagnostic information — what a listing claims is "the result of
    running with this config", not the config itself (config equality is
    already separately guaranteed by BT-9's `reproducibility_key`), and
    warning text can change with the engine version, unrelated to the
    performance claim. The canonical serialization rules (sorted keys, fixed
    separators, `ensure_ascii=True`) match BT-9's
    `domain/reproducibility.py` — if reproducibility-key-family hashes use
    different normalization per leaf, it weakens the "same input = same
    hash" contract.
    """
    if not result.equity_curve:
        raise ValueError("compute_result_hash: 빈 equity_curve로는 해시를 낼 수 없습니다")
    payload = {
        "schema": RESULT_HASH_SCHEMA,
        "fills": [f.model_dump(mode="json") for f in result.fills],
        "equity_curve": [e.model_dump(mode="json") for e in result.equity_curve],
        "metrics": result.metrics.model_dump(mode="json"),
    }
    canonical = json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class ListingBacktestClaim:
    """A performance claim the seller posted on a listing — corresponds to
    §3.5 `ScriptListing`'s `result_hash` (MP-1 has not yet finalized the
    contract itself, so this leaf defines only the minimum fields needed
    for verification)."""

    listing_id: int
    claimed_result_hash: str

    def __post_init__(self) -> None:
        if not self.claimed_result_hash or not self.claimed_result_hash.strip():
            raise ValueError(
                f"ListingBacktestClaim(listing_id={self.listing_id}): "
                "claimed_result_hash가 비어 있습니다"
            )


@dataclass(frozen=True)
class VerificationOutcome:
    listing_id: int
    is_verified: bool
    claimed_result_hash: str
    reproduced_result_hash: str
    error_code: str | None

    def raise_if_unverified(self) -> None:
        """For use as a gate (e.g. right before a reputation tally) — blocks
        immediately with an exception on mismatch."""
        if not self.is_verified:
            raise ListingUnverifiedError(self)


class ListingUnverifiedError(Exception):
    def __init__(self, outcome: VerificationOutcome) -> None:
        self.outcome = outcome
        super().__init__(
            f"{MP_UNVERIFIED_RESULT}: listing_id={outcome.listing_id} "
            f"주장 해시={outcome.claimed_result_hash!r} != "
            f"재현 해시={outcome.reproduced_result_hash!r}"
        )


def verify_listing_backtest(
    claim: ListingBacktestClaim,
    reproduced_result: BacktestResult,
) -> VerificationOutcome:
    """Diffs the seller's claimed hash against the hash of the
    `BacktestResult` the platform independently reproduced, bit-for-bit
    (full sha256 hex match).

    Match -> `is_verified=True`, `error_code=None` — counting toward
    `reputation.reproduced_backtests` is allowed. Mismatch ->
    `is_verified=False`, `error_code=MP_UNVERIFIED_RESULT` — the caller must
    not reflect this entry in the tally (see `count_verified_backtests()`).
    """
    reproduced_hash = compute_result_hash(reproduced_result)
    is_verified = claim.claimed_result_hash == reproduced_hash
    return VerificationOutcome(
        listing_id=claim.listing_id,
        is_verified=is_verified,
        claimed_result_hash=claim.claimed_result_hash,
        reproduced_result_hash=reproduced_hash,
        error_code=None if is_verified else MP_UNVERIFIED_RESULT,
    )


def count_verified_backtests(outcomes: Sequence[VerificationOutcome]) -> int:
    """The `reproduced_backtests` input to the §4.3 reputation formula —
    counts only entries that passed verification.

    Entries flagged `MP_UNVERIFIED_RESULT` are all excluded (fail-closed)
    regardless of whether the cause is forgery or plain engine drift —
    reflecting a suspicious reproduction in reputation would reproduce
    exactly the problem this leaf exists to prevent (uncritical acceptance
    of self-reported performance).
    """
    return sum(1 for outcome in outcomes if outcome.is_verified)


__all__ = [
    "MP_UNVERIFIED_RESULT",
    "RESULT_HASH_SCHEMA",
    "ListingBacktestClaim",
    "ListingUnverifiedError",
    "VerificationOutcome",
    "compute_result_hash",
    "count_verified_backtests",
    "verify_listing_backtest",
]
