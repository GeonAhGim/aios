"""ledger top-level re-export surface for cross-aggregate consumers.

Spec: task-5418 (task-5404 분할 4/4), `.importlinter` boundary:foundation-aggregates.
Other foundation aggregates may not import `ledger.domain.*` or
`ledger.adapters.*` directly (교차 애그리게잇 경계) — they import the
symbols they need from here instead. This module only re-exports; it adds
no behavior of its own, so the runtime effect of switching an import from
`ledger.domain.chart_of_accounts.X` to `ledger.api.X` is nil.

Not `contracts/v1.py`: that file is the versioned public schema surface and
by convention (107번, rule 103) never imports `domain/` — mixing that
constraint with plain re-exports of pure domain functions would either
force copies out of `domain/` for no benefit or quietly smuggle `domain`
imports behind the contracts file. This module carries no version/schema
guarantee, only import-path indirection.
"""
from __future__ import annotations

from src.foundation.ledger.domain.chart_of_accounts import (
    PLATFORM_CASH_CLEARING,
    account_type,
    allows_negative,
    user_account,
)

__all__ = ["PLATFORM_CASH_CLEARING", "account_type", "allows_negative", "user_account"]
