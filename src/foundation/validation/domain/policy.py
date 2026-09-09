"""L4_strategy_portfolio_backtest_v1.0.md §2 row 156 / §9 L36 --
versioned validation policy (thresholds) and its content hash.

`ValidationPolicy` pins every threshold a validation bundle is judged
against (§3.5-A). "Which policy produced this FAIL" must always be
answerable (R2), so the whole policy is content-addressed via
`policy_hash()`. Hashing reuses R-01 `src.core.risk.hashing.canonical_json`
/`sha256_hex` -- no new normalization rule is introduced here, matching
the pattern already used by `src.core.portfolio.config` and
`src.foundation.backtest.domain.models` for their own hash methods.
"""
from __future__ import annotations

from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict

from src.core.risk.hashing import canonical_json, sha256_hex

REQUIRED_CHECKS: tuple[str, ...] = (
    "point_in_time",
    "backtest",
    "oos_walk_forward",
    "robustness",
    "stress_capacity",
    "failure_conditions",
)

REQUIRED_STRESS_SCENARIOS: tuple[str, ...] = (
    "COST_X2",
    "COST_X3",
    "SLIPPAGE_PLUS_50BPS",
    "WORST_5_DAYS_REMOVED",
    "GAP_2PCT",
)


class ValidationPolicy(BaseModel):
    """Fixed field set per §2 row 156 / §3.5-A -- a field name or default
    drifting from the spec table is a rejection, not a style nit."""

    model_config = ConfigDict(frozen=True)

    policy_version: Literal["vp-v1"] = "vp-v1"
    required_checks: tuple[str, ...] = REQUIRED_CHECKS
    allow_zero_cost: bool = False
    min_oos_windows: int = 3
    oos_mode: Literal["ANCHORED", "ROLLING"] = "ROLLING"
    purge_bars: int = 24
    embargo_bars: int = 24
    min_grid_points: int = 4
    max_pbo: Decimal = Decimal("0.5")
    min_dsr: Decimal = Decimal("0.95")
    max_param_isolation: Decimal = Decimal("0.5")
    required_stress: tuple[str, ...] = REQUIRED_STRESS_SCENARIOS
    run_timeout_seconds: int = 1800

    def policy_hash(self) -> str:
        """Deterministic content hash of every field. Two policies built
        with identical values hash identically; changing any single field
        (e.g. `max_pbo`) changes the hash -- there is no cache reuse across
        policy edits (§3.5-A reproducibility note)."""
        return sha256_hex(canonical_json(self.model_dump(mode="python")))


__all__ = ["REQUIRED_CHECKS", "REQUIRED_STRESS_SCENARIOS", "ValidationPolicy"]
