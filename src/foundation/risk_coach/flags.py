"""U-8 feature flag -- gates `POST /risk-coach/position-size` behind
`FF_U8_RISK_COACH`.

Off by default (staged rollout ahead of production exposure, ADR-2026-09-09-B
Decision C §U common DoD: "every U leaf ships behind a feature flag for
staged rollout"). Mirrors `src.foundation.automation.flags`'s pattern
(off-by-default `os.environ` lookup) rather than importing it -- that module
gates an unrelated leaf (U-4a rule engine) and each leaf keeps its own copy
to avoid an accidental flip if one leaf's flag direction ever changes.
"""

from __future__ import annotations

import os

__all__ = [
    "FEATURE_FLAG_NAME",
    "RiskCoachFeatureDisabledError",
    "flag_enabled",
    "require_flag_enabled",
]

FEATURE_FLAG_NAME = "FF_U8_RISK_COACH"


class RiskCoachFeatureDisabledError(Exception):
    """The `FF_U8_RISK_COACH` feature flag is off -- the router maps this to
    404 (does not pretend the endpoint exists), the same shape as
    `AssistantFeatureDisabledError`."""


def flag_enabled() -> bool:
    return os.environ.get(FEATURE_FLAG_NAME, "0") == "1"


def require_flag_enabled() -> None:
    if not flag_enabled():
        raise RiskCoachFeatureDisabledError(f"{FEATURE_FLAG_NAME} is off")
