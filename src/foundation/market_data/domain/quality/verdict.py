"""LA-6 — Issue set → batch verdict (ACCEPT/PARTIAL/QUARANTINE/REJECT).

Spec: docs/specs/L4_market_data_positions_ledger_v1.0.md#§2.2 LA-6, §4.1, §9.2 LA-6.

Fail-closed rule table (§4.1): issues with REJECT severity are counted as
per-candle quarantine targets (`rejected`); when their ratio exceeds 20%
(Draft), the entire batch is quarantined per the "no partial storage"
principle (`quarantined=total`, `accepted=0`).
`total<=0` means unscorable state, so fail-closed default returns REJECT
(not a batch rule specified in §4.1 table, but a defensive default — unverified).
"""

from __future__ import annotations

from decimal import Decimal

from src.foundation.market_data.contracts.v1 import QualityIssue, QualityVerdict, Severity, Verdict

__all__ = ["decide"]

_REJECT_RATIO_LIMIT = Decimal("0.20")


def decide(issues: list[QualityIssue], total: int) -> QualityVerdict:
    if total <= 0:
        return QualityVerdict(
            verdict=Verdict.REJECT, accepted=0, quarantined=0, rejected=0, issues=list(issues)
        )

    rejected = sum(1 for issue in issues if issue.severity == Severity.REJECT)
    if Decimal(rejected) / Decimal(total) > _REJECT_RATIO_LIMIT:
        return QualityVerdict(
            verdict=Verdict.QUARANTINE,
            accepted=0,
            quarantined=total,
            rejected=0,
            issues=list(issues),
        )

    accepted = max(total - rejected, 0)
    verdict = Verdict.ACCEPT if not issues else Verdict.PARTIAL
    return QualityVerdict(
        verdict=verdict, accepted=accepted, quarantined=0, rejected=rejected, issues=list(issues)
    )
