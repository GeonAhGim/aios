"""RD-1 deepen (task-2904) — RESEARCH_DATA_SOURCE_EVAL.md DoD gate (pure).

Spec: docs/specs/L4_research_data_and_market_ecosystem_v1.0.md §9 RD-1.
Parsing lives in `source_eval_parse.py`; this module applies DoD invariants
(a)/(c)/(e)/(f) fail-closed. No I/O. Public API re-exports parse types so
callers/tests keep importing from this module.
"""
from __future__ import annotations

from src.foundation.research_data.domain.source_eval_parse import (
    EXPECTED_ADMISSION,
    LAYER_A_SOURCE_IDS,
    SourceEvalGateError,
    SourceEvalRecord,
    SourceEvalReport,
    parse_source_eval_markdown,
)

__all__ = [
    "LAYER_A_SOURCE_IDS",
    "EXPECTED_ADMISSION",
    "SourceEvalGateError",
    "SourceEvalRecord",
    "SourceEvalReport",
    "parse_source_eval_markdown",
    "assert_source_eval_gate",
]


def assert_source_eval_gate(markdown: str) -> SourceEvalReport:
    """Full RD-1 DoD gate. Returns the report on success; raises on violation."""
    report = parse_source_eval_markdown(markdown)

    for record in report.records:
        if not record.has_blockquote:
            raise SourceEvalGateError(
                "MISSING_QUOTE",
                f"{record.source_id}: DoD (a) requires a blockquote citation",
            )
        if not record.has_url:
            raise SourceEvalGateError(
                "MISSING_URL",
                f"{record.source_id}: DoD (a) requires a source URL",
            )
        if not record.has_check_date:
            raise SourceEvalGateError(
                "MISSING_DATE",
                f"{record.source_id}: DoD (a) requires a YYYY-MM-DD check date",
            )
        if record.rate_limit_status == "unconfirmed" and record.admission != "deny":
            raise SourceEvalGateError(
                "UNCONFIRMED_RATE_LIMIT_ALLOWED",
                f"{record.source_id}: rate limit 미확인 must force 반입 금지",
            )
        expected = EXPECTED_ADMISSION[record.source_id]
        if record.admission != expected:
            raise SourceEvalGateError(
                "ADMISSION_DRIFT",
                f"{record.source_id}: section admission={record.admission} "
                f"!= frozen EXPECTED_ADMISSION={expected}",
            )

    if report.allow_table_ids & report.deny_table_ids:
        raise SourceEvalGateError(
            "TABLE_OVERLAP",
            f"source in both §9.1 and §9.2: "
            f"{sorted(report.allow_table_ids & report.deny_table_ids)}",
        )
    table_union = report.allow_table_ids | report.deny_table_ids
    expected_set = frozenset(LAYER_A_SOURCE_IDS)
    if table_union != expected_set:
        raise SourceEvalGateError(
            "TABLE_COVERAGE",
            f"§9 tables cover {sorted(table_union)} != LAYER_A {list(LAYER_A_SOURCE_IDS)}",
        )

    by_id = {r.source_id: r for r in report.records}
    for source_id in report.allow_table_ids:
        if by_id[source_id].admission != "allow":
            raise SourceEvalGateError(
                "TABLE_SECTION_MISMATCH",
                f"{source_id}: in §9.1 allow table but section conclusion is deny",
            )
    for source_id in report.deny_table_ids:
        if by_id[source_id].admission != "deny":
            raise SourceEvalGateError(
                "TABLE_SECTION_MISMATCH",
                f"{source_id}: in §9.2 deny table but section conclusion is allow",
            )

    for needle in ("OpenDART", "ECOS", "KOSIS"):
        if needle not in markdown or "키" not in markdown:
            raise SourceEvalGateError(
                "MISSING_KEY_NOTE",
                f"DoD (f): key-issuance note for {needle} missing",
            )

    return report
