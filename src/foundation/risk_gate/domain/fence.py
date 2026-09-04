"""fence 비교 순수 규칙 — I/O 없음(zone purity).

Spec: docs/specs/L4_risk_and_safety_v1.0.md §2.4 `domain/fence.py`, §3.6.
"""
from __future__ import annotations

from uuid import UUID

from src.foundation.risk_gate.domain.models import GLOBAL_SCOPE_REF, FenceSnapshot, SafetyScope

FencePair = tuple[SafetyScope, str]


def fence_pairs_for(
    tenant_id: UUID, provider_code: str, execution_ref: str
) -> tuple[FencePair, ...]:
    """§3.6 "pairs = fence_pairs_for(...)" — 5쌍 고정 순서. 순서 자체가
    계약은 아니지만(멤버십만 쓰인다) 호출부마다 다른 순서로 만들면 로그·감사
    비교가 흔들리므로 이 순서를 표준으로 고정한다."""
    return (
        (SafetyScope.GLOBAL, GLOBAL_SCOPE_REF),
        (SafetyScope.PROVIDER, provider_code),
        (SafetyScope.TENANT, str(tenant_id)),
        (SafetyScope.ACCOUNT, str(tenant_id)),
        (SafetyScope.STRATEGY_DEPLOYMENT, execution_ref),
    )


def is_stale(observed: FenceSnapshot, current: FenceSnapshot) -> bool:
    """§3.6 "F0/F1/F2 비교는 토큰 증가만 stale로 본다(감소는 DB 제약상
    불가)". `current`에만 있고 `observed`에 없던 pair도 0→N 증가로 취급한다
    (관측 시점엔 activate된 적이 없어 행 자체가 없었던 scope)."""
    return any(
        current_token > observed.tokens.get(pair, 0)
        for pair, current_token in current.tokens.items()
    )
