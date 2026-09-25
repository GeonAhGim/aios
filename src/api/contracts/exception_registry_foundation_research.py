"""L4 §2.3(C) — domain exception -> ErrorCode mapping, the
`foundation.research_data.*` bucket.

`exception_registry_foundation.py` hit the P6.line_cap architecture guard
(300 lines) again (task-2709 RD-8), for the same reason it was already split
before (see `exception_registry_foundation_mandates.py`) -- so the
research_data cluster is carved out here. The public API is unchanged:
`exception_registry_foundation.py` appends this module's
`EXCEPTION_MAP_RESEARCH` onto its own `EXCEPTION_MAP_FOUNDATION` (every
other module still imports only from
`exception_registry_foundation`/`exception_mapping`).
"""

from __future__ import annotations

from src.api.contracts.error_codes import ErrorCode
from src.foundation.research_data.application.authorize_access import (
    ResearchItemNotFoundError,
    ResearchSourceAccessDeniedError,
)

EXCEPTION_MAP_RESEARCH: list[tuple[type[Exception], ErrorCode]] = [
    # RD-8(task-2709) -- research_data.py read API. Cross-tenant/unknown
    # item_id fold into the same 404; source contract (DC-27, DC-9 lineage)
    # denial is a separate 403 (existence is already confirmed by then).
    (ResearchItemNotFoundError, ErrorCode.RESOURCE_NOT_FOUND),
    (ResearchSourceAccessDeniedError, ErrorCode.AUTHZ_FORBIDDEN),
]

__all__ = ["EXCEPTION_MAP_RESEARCH"]
