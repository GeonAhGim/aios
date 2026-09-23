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


def methodology_hash(m: Methodology) -> str:
    """Exclude the `methodology_hash` field itself from hash input (prevent self-reference)."""
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
