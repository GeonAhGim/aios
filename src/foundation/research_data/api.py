"""research_data top-level re-export surface for cross-aggregate consumers.

Spec: task-5418 (task-5404 분할 4/4), `.importlinter` boundary:foundation-aggregates.
Other foundation aggregates may not import `research_data.domain.*` or
`research_data.adapters.*` directly (교차 애그리게잇 경계) — they import the
symbols they need from here instead. This module only re-exports; it adds
no behavior of its own, so the runtime effect of switching an import from
`research_data.adapters.X` to `research_data.api` is nil.

Not `contracts/v1.py`: that file is the versioned public schema surface and
by convention (107번, rule 103) never imports `domain/`/`adapters/` — mixing
that constraint with plain re-exports would either force copies out of
`adapters/` for no benefit or quietly smuggle those imports behind the
contracts file. This module carries no version/schema guarantee, only
import-path indirection.
"""
from __future__ import annotations

from src.foundation.research_data.adapters.dsl_query import research_builtins

__all__ = ["research_builtins"]
