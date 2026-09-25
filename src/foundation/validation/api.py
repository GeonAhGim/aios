"""validation top-level re-export surface for cross-aggregate consumers.

Spec: task-5418 (task-5404 분할 4/4), `.importlinter` boundary:foundation-aggregates.
Other foundation aggregates may not import `validation.domain.*` or
`validation.adapters.*` directly (교차 애그리게잇 경계) — they import the
symbols they need from here instead. This module only re-exports; it adds
no behavior of its own, so the runtime effect of switching an import from
`validation.domain.X` to `validation.api` is nil.

Not `contracts/v1.py`: that file is the versioned public schema surface and
by convention (107번, rule 103) never imports `domain/` — mixing that
constraint with plain re-exports of pure domain functions would either
force copies out of `domain/` for no benefit or quietly smuggle `domain`
imports behind the contracts file. This module carries no version/schema
guarantee, only import-path indirection.
"""
from __future__ import annotations

from src.foundation.validation.domain.check_result import CheckResult
from src.foundation.validation.domain.models import Outcome
from src.foundation.validation.domain.rules import evaluate_bundle

__all__ = ["CheckResult", "Outcome", "evaluate_bundle"]
