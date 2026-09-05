"""시뮬 venue 프로파일(L4 명세 §2-F).

Spec: docs/specs/L4_execution_oms_and_exchange_v1.0.md §2-F, §9 L4-22.

참조 거래소 프로파일을 복제하고 `venue="paper_sim"`으로 바꾼다. `verified`는
항상 `"ESTIMATED"` — 시뮬레이터는 어떤 capability도 라이브 검증하지 않는다
(§10 정직 표기). 참조 객체는 변경하지 않는다(깊은 복사).
"""
from __future__ import annotations

from src.services.oms.domain.venue_profile import VenueCapabilityProfile

PAPER_VENUE = "paper_sim"


def profile_for(reference: VenueCapabilityProfile) -> VenueCapabilityProfile:
    if reference.venue == PAPER_VENUE:
        raise ValueError("paper_sim 프로파일을 다시 감쌀 수 없습니다(참조 거래소 프로파일 필요).")
    return reference.model_copy(update={"venue": PAPER_VENUE, "verified": "ESTIMATED"}, deep=True)
