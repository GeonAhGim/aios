"""market_data top-level re-export surface for cross-aggregate consumers.

Spec: task-5416 (task-5404 분할 2/4), `.importlinter` boundary:foundation-aggregates.
Other foundation aggregates may not import `market_data.domain.*` or
`market_data.adapters.*` directly (교차 애그리게잇 경계) — they import the
symbols they need from here instead. This module only re-exports; it adds
no behavior of its own, so the runtime effect of switching an import from
`market_data.domain.X` to `market_data.api` is nil.

Not `contracts/v1.py`: that file is the versioned public schema surface and
by convention (107번, rule 103) never imports `domain/` — mixing that
constraint with plain re-exports of pure domain functions would either
force copies out of `domain/` for no benefit or quietly smuggle `domain`
imports behind the contracts file. This module carries no version/schema
guarantee, only import-path indirection.
"""
from __future__ import annotations

from src.foundation.market_data.domain.candle_columns import CandleColumns
from src.foundation.market_data.domain.corporate_action.opendart_filing import (
    FilingParseError,
    OpenDartFiling,
    normalize_filing,
)
from src.foundation.market_data.domain.coverage.registry import merge_spans
from src.foundation.market_data.domain.entitlement.source_contract import (
    DataUse,
    SourceContractGrant,
    permits_use,
)
from src.foundation.market_data.domain.reference.symbol_normalizer import (
    SymbolNormalizationError,
    to_canonical,
)

__all__ = [
    "CandleColumns",
    "DataUse",
    "FilingParseError",
    "OpenDartFiling",
    "SourceContractGrant",
    "SymbolNormalizationError",
    "merge_spans",
    "normalize_filing",
    "permits_use",
    "to_canonical",
]
