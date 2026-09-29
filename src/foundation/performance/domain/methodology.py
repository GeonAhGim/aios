"""Performance methodology — versioned hashing (R2).

Spec: docs/specs/L4_strategy_portfolio_backtest_v1.0.md §2.6.

Default `pm-v1`: TWR splits sub-periods at each cashflow point and
geometrically links them (domain/twr.py, L46). MWR solves IRR via
bisection (max 200 iterations, tolerance 1e-10) (domain/mwr.py, L46).
The risk-free rate is fixed at 0 — actual risk-free benchmark wiring
is out of scope for this leaf (if needed later, review separately; the
session's iterative principle is to not build ahead). Annualised
calculations always require the caller to specify
`periods_per_year` (implicit assumptions prohibited). The benchmark is
pinned to the mandate value at the start of the statement period (no
retroactive adjustment if the mandate changes mid-period — enforced by
`assert_benchmark_pinned` in domain/rules.py, L46).
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from decimal import Decimal

from src.foundation.performance.domain.models import Methodology

MWR_MAX_ITERATIONS = 200
MWR_TOLERANCE = Decimal("1E-10")


class MethodologyValidationError(ValueError):
    """A `Methodology` violates a defining-field invariant and cannot be hashed.

    `periods_per_year` must stay a positive, explicit annualisation base (the
    module docstring above prohibits implicit assumptions); `risk_free_rate_pct`
    must stay finite (same precision invariant as `rules.assert_precision`);
    `version`/`twr_method`/`mwr_method` must stay non-blank, since a blank value
    would silently collide across distinct methodologies and break the WORM
    versioning rule in `models.Methodology`.
    """

    def __init__(self, reason_code: str, detail: str) -> None:
        super().__init__(f"{reason_code}: {detail}")
        self.reason_code = reason_code


def _assert_valid(m: Methodology) -> None:
    if m.periods_per_year <= 0:
        raise MethodologyValidationError(
            "INTEGRITY_METHODOLOGY_PERIODS_PER_YEAR",
            f"periods_per_year={m.periods_per_year}는 0보다 커야 합니다.",
        )
    if not m.risk_free_rate_pct.is_finite():
        raise MethodologyValidationError(
            "INTEGRITY_CURRENCY_PRECISION",
            f"risk_free_rate_pct={m.risk_free_rate_pct}는 유한값이어야 합니다.",
        )
    for field_name, value in (
        ("version", m.version),
        ("twr_method", m.twr_method),
        ("mwr_method", m.mwr_method),
    ):
        if not value.strip():
            raise MethodologyValidationError(
                "INTEGRITY_METHODOLOGY_BLANK_FIELD",
                f"{field_name}는 빈 값일 수 없습니다.",
            )


def methodology_hash(m: Methodology) -> str:
    """Exclude the `methodology_hash` field itself from hash input (prevent self-reference)."""
    _assert_valid(m)
    payload = {
        "version": m.version,
        "twr_method": m.twr_method,
        "mwr_method": m.mwr_method,
        "risk_free_rate_pct": str(m.risk_free_rate_pct),
        "periods_per_year": m.periods_per_year,
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode("utf-8")).hexdigest()


_provisional = Methodology(
    version="pm-v1",
    methodology_hash="",
    twr_method="PERIOD_LINKED_CASHFLOW_AT_START",
    mwr_method="IRR_BISECTION",
    risk_free_rate_pct=Decimal("0"),
    periods_per_year=252,
)
DEFAULT_METHODOLOGY = replace(_provisional, methodology_hash=methodology_hash(_provisional))
