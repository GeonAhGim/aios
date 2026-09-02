"""LA-6 — 이슈 집합 → 배치 판정(ACCEPT/PARTIAL/QUARANTINE/REJECT).

Spec: docs/specs/L4_market_data_positions_ledger_v1.0.md#§2.2 LA-6, §4.1, §9.2 LA-6.

fail-closed 규칙표(§4.1) 그대로: REJECT 심각도 이슈는 캔들별 격리 대상으로
세고(`rejected`), 그 비율이 20%(Draft)를 넘으면 "부분 저장 금지" 원칙에
따라 배치 전체를 quarantine한다(`quarantined=total`, `accepted=0`).
`total<=0`은 채점 불가 상태라 fail-closed 기본값으로 REJECT를 반환한다
(§4.1 표에 명시된 배치 규칙은 아니며, 방어적 기본값 — 미검증).
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

    rejected = sum(1 for issue in issues if issue.severity is Severity.REJECT)
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
