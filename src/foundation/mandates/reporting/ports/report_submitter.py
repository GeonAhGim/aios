"""L4_compliance_and_regulatory_v1.0.md#9 CM-16 -- immutable (WORM) storage
port for a generated compliance report.

Spec §10: the real regulatory submission adapter (domestic reporting
system, MiFID) is MVP-2 -- MVP-1 stops at producing the report. This leaf's
port only covers *storing* the report artifact `application/
generate_report.py` builds; it never calls out to an external regulator/
exchange system. A future MVP-2 submission adapter reads the stored row
back through `get_by_trade_id` instead of being handed the report directly
by `generate_report()`.

Follows the `src/foundation` convention (standard 71 §4): domain/application
code depends only on this `Protocol`, never on a concrete adapter. `store()`
is insert-or-get keyed by `trade_id`, the same append-only idempotency shape
as `RouteDecisionRepository.insert_or_get`
(`src/foundation/ems/ports/route_decision_repository.py`) -- there is no
update method, so a genuine content change under an already-stored
`trade_id` cannot silently overwrite the prior row; the caller
(`generate_report()`) detects that drift by comparing `content_hash` on the
row `store()` returns.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol, runtime_checkable
from uuid import UUID

__all__ = ["GeneratedReportRecord", "ReportSubmitterPort"]


@dataclass(frozen=True)
class GeneratedReportRecord:
    """One persisted compliance report row.

    `fields` is the exact submission-shaped string map (`trade_report.
    to_domestic_report_fields()` plus best-execution fields,
    `application/generate_report.py`) that `content_hash` covers --
    regenerating from the same `trade_report`/`best_execution` inputs always
    reproduces the same `fields` and therefore the same `content_hash`
    (CM-16 DoD)."""

    trade_id: UUID
    order_id: UUID
    content_hash: str
    fields: dict[str, str]
    generated_at: datetime


@runtime_checkable
class ReportSubmitterPort(Protocol):
    """Append-only (WORM) report storage -- no update/delete method."""

    async def store(self, record: GeneratedReportRecord) -> GeneratedReportRecord:
        """Insert one row for `record.trade_id`, or -- if a row for that
        `trade_id` already exists -- return the existing row unchanged
        (concurrent regenerate calls for the same trade must not create a
        second row, and must not raise on the losing side)."""
        ...

    async def get_by_trade_id(self, trade_id: UUID) -> GeneratedReportRecord | None: ...
