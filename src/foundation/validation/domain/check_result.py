"""L4_strategy_portfolio_backtest_v1.0.md §2 row 157 / §9 L36 --
common result shape shared by every validation check (point_in_time,
backtest, oos_walk_forward, robustness, stress_capacity,
failure_conditions).

`HARD_FAIL_CODES` is the closed set of codes from §3.5-A's hard-fail
table (I-07: hard-fail conditions must be real, computed outcomes, not
free-form strings a check author can invent). `CheckResult` rejects any
`hard_fail_reasons` entry outside that set at construction time so a
typo or a new ad-hoc code can never silently downgrade to a soft
obligation.

This module only defines the type. `src.foundation.validation.domain.rules`
(task-1114's `evaluate_validation_policy`, kept as-is) still owns the
3-tuple bundle-evaluation contract -- nothing here changes it.
"""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, field_validator

from src.foundation.validation.domain.models import Outcome

HARD_FAIL_CODES: frozenset[str] = frozenset(
    {
        "INTEGRITY_FUTURE_DATA",
        "INTEGRITY_LINEAGE_MISSING",
        "INTEGRITY_BAR_ORDER",
        "VALIDATION_COST_MODEL_REQUIRED",
        "BACKTEST_LOOKAHEAD_VIOLATION",
        "VALIDATION_OOS_LEAKAGE",
        "VALIDATION_OOS_INSUFFICIENT",
        "VALIDATION_NONREPRODUCIBLE_CONFIG",
        "VALIDATION_SCENARIO_MISSING",
        "VALIDATION_NO_INVALIDATION_CRITERIA",
    }
)


class CheckResult(BaseModel):
    """Fixed field set per §2 row 157 / §3.5-A pseudocode."""

    model_config = ConfigDict(frozen=True)

    check_type: str
    outcome: Outcome
    metrics: dict[str, Any]
    warnings: list[str] = []
    hard_fail_reasons: list[str] = []
    obligations: list[str] = []
    evidence_refs: list[str] = []
    result_hash: str
    policy_version: str
    overfitting_version: str | None = None
    schema_version: Literal["chk-v1"] = "chk-v1"

    @field_validator("hard_fail_reasons")
    @classmethod
    def _reject_unknown_hard_fail_codes(cls, value: list[str]) -> list[str]:
        unknown = [code for code in value if code not in HARD_FAIL_CODES]
        if unknown:
            raise ValueError(
                f"hard_fail_reasons contains codes outside HARD_FAIL_CODES: {unknown}"
            )
        return value


__all__ = ["HARD_FAIL_CODES", "CheckResult"]
