"""U-4a feature flag -- gates the whole no-code automation rule engine (rule
create/list/cancel/preview, batch evaluation, action execution) behind
`FF_U4A_RULE_ENGINE`.

Off by default (staged rollout ahead of production exposure, ADR-2026-09-09-B
Decision C §U common DoD: "every U leaf ships behind a feature flag for
staged rollout"). Mirrors `src/api/routers/assistant.py::flag_enabled`'s
off-by-default direction rather than importing it -- that helper is private
to the assistant router, and `background_loops.flag_enabled` has the
opposite default direction (on by default), so each leaf keeps its own copy
to avoid an accidental flip (see that router's own docstring for the same
reasoning).

The `src/api/routers/rules.py` router itself has not landed yet (still a
follow-up leaf per `contracts/v1.py`'s module docstring and
`ports/repository.py`'s), so there is no endpoint to gate today. This module
gates the application-layer entry points a future router will call directly
(`create_rule`/`list_rules`/`cancel_rule`/`preview_rule` raise
`RuleEngineFeatureDisabledError` -- the same shape the router will map to a
404, mirroring `AssistantFeatureDisabledError`), plus the two spots that can
reach live-trading actions without any router at all
(`evaluate_active_rules`/`execute_action`, which no-op instead of raising so
a batch loop over many tenants' rules never crashes on a disabled flag).
"""

from __future__ import annotations

import os

__all__ = [
    "FEATURE_FLAG_NAME",
    "RuleEngineFeatureDisabledError",
    "flag_enabled",
    "require_flag_enabled",
]

FEATURE_FLAG_NAME = "FF_U4A_RULE_ENGINE"


class RuleEngineFeatureDisabledError(Exception):
    """The `FF_U4A_RULE_ENGINE` feature flag is off -- the future API router
    leaf maps this to 404 (does not pretend the rule engine exists), the
    same shape as `AssistantFeatureDisabledError`."""


def flag_enabled() -> bool:
    return os.environ.get(FEATURE_FLAG_NAME, "0") == "1"


def require_flag_enabled() -> None:
    if not flag_enabled():
        raise RuleEngineFeatureDisabledError(f"{FEATURE_FLAG_NAME} is off")
