"""fence 스냅샷 조회 — pairs 계산과 1쿼리 조회를 묶는 단일 진입점.

Spec: docs/specs/L4_risk_and_safety_v1.0.md §2.4 `application/read_fence.py`,
§3.6 F0/F1/F2.
"""
from __future__ import annotations

from uuid import UUID

from src.foundation.risk_gate.domain.fence import fence_pairs_for
from src.foundation.risk_gate.domain.models import FenceSnapshot
from src.foundation.risk_gate.ports.repository import RiskGateRepository


async def read_fence_snapshot(
    repo: RiskGateRepository,
    *,
    tenant_id: UUID,
    provider_code: str,
    execution_ref: str,
) -> FenceSnapshot:
    """§3.6 시퀀스의 F0/F1/F2 각 지점이 공유하는 호출부 — 매번 직접
    `fence_pairs_for` + `read_fences`를 조합하면 5쌍 순서·구성이 호출부마다
    흩어질 수 있어 이 함수로만 스냅샷을 뜬다."""
    pairs = fence_pairs_for(tenant_id, provider_code, execution_ref)
    return await repo.read_fences(pairs)
