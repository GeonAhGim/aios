"""L4_compliance_and_regulatory_v1.0.md#9 CM-16 -- build one compliance
report from a CM-15 `TradeReportRecord` plus its CM-14 `BestExecutionEvidence`,
and persist it immutably through `ports/report_submitter.py`.

`_report_fields()` reuses CM-15's `to_domestic_report_fields()` verbatim and
only appends the best-execution fields -- it never re-derives the trade
fields or re-implements R-01 hashing (`src.core.risk.hashing`), the same
discipline CM-13's `explain()` and CM-15's `normalize_trade_report()` follow.
Because `fields` is built solely from the two domain records (no clock, no
randomness), two `generate_report()` calls for the same inputs always
compute the same `content_hash` before `store()` is even called -- the
"regenerate from the same inputs -> identical hash" DoD holds independently
of whatever `ReportSubmitterPort` implementation is wired in.
"""

from __future__ import annotations

from datetime import datetime

from src.core.risk.hashing import canonical_json, sha256_hex
from src.foundation.mandates.reporting.domain.best_execution import BestExecutionEvidence
from src.foundation.mandates.reporting.domain.trade_report import (
    TradeReportRecord,
    to_domestic_report_fields,
)
from src.foundation.mandates.reporting.ports.report_submitter import (
    GeneratedReportRecord,
    ReportSubmitterPort,
)

__all__ = ["GenerateReportInputError", "ReportDriftError", "generate_report"]


class GenerateReportInputError(ValueError):
    """Malformed or mismatched input; fail-closed (I-02) -- never build a
    reportable-looking document from evidence that does not agree."""


class ReportDriftError(RuntimeError):
    """`store()` returned an existing row for `trade_id` whose `content_hash`
    disagrees with the one just computed -- the same trade already has a
    different report on file. Silently returning the stored row here would
    let a WORM record be replaced by whichever caller asks second; per I-04's
    content-hash-addressed immutability principle, this leaf refuses instead
    of coalescing two different report bodies under one `trade_id`."""


def _report_fields(
    trade_report: TradeReportRecord, best_execution: BestExecutionEvidence
) -> dict[str, str]:
    fields = dict(to_domestic_report_fields(trade_report))
    fields["집행_벤치마크"] = best_execution.benchmark.value
    fields["집행_벤치마크가격"] = str(best_execution.benchmark_price)
    fields["집행_슬리피지_bp"] = str(best_execution.slippage_bps)
    fields["집행_사유코드"] = ",".join(best_execution.reason_codes)
    fields["집행_경로판단ID"] = str(best_execution.route_decision_id)
    return fields


async def generate_report(
    store: ReportSubmitterPort,
    *,
    trade_report: TradeReportRecord,
    best_execution: BestExecutionEvidence,
    generated_at: datetime,
) -> GeneratedReportRecord:
    """§9 CM-16 public contract. `best_execution` must cite the same order as
    `trade_report` -- a report is never assembled from two unrelated fills."""
    if best_execution.order_id != trade_report.order_id:
        raise GenerateReportInputError(
            "best_execution.order_id does not match trade_report.order_id"
        )
    if generated_at.tzinfo is None:
        raise GenerateReportInputError("generated_at must be tz-aware")

    fields = _report_fields(trade_report, best_execution)
    content_hash = sha256_hex(canonical_json(fields))
    record = GeneratedReportRecord(
        trade_id=trade_report.trade_id,
        order_id=trade_report.order_id,
        content_hash=content_hash,
        fields=fields,
        generated_at=generated_at,
    )
    stored = await store.store(record)
    if stored.content_hash != content_hash:
        raise ReportDriftError(
            f"trade {trade_report.trade_id} already has a stored report with a "
            f"different content_hash (stored={stored.content_hash} new={content_hash})"
        )
    return stored
