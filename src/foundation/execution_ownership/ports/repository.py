"""EO-02 — 실행 소유권(리스) 저장소 포트.

Spec: docs/specs/L4_execution_ownership_and_safety_gate_wiring_v1.0.md
§2-A, §3.2. domain은 이 Protocol만 알고, 실제 구현(adapters/)은 모른다
(71번 §4)."""
from __future__ import annotations

from typing import Protocol


class ExecutionLeaseRepository(Protocol):
    async def acquire_or_renew_many(
        self, execution_ids: list[int], *, owner_id: str, ttl_seconds: float
    ) -> set[int]:
        """execution_ids 중 리스를 획득했거나 이미 보유 중(갱신 성공)인 것만
        반환한다. 다른 owner_id가 만료 전 리스를 쥐고 있으면 그 execution_id는
        반환하지 않는다(§4.1 — "유효한 리스를 가진 프로세스 하나에서만 동시에
        tick된다"). 갱신 실패는 예외를 던지지 않고 반환 집합에서 빠지는
        형태로만 신호한다 — 호출자는 이번 주기에 그 execution_id를 건너뛴다."""
        ...

    async def release_all(self, owner_id: str) -> int:
        """이 owner_id가 쥔 리스를 전부 해제(삭제)하고 해제한 행 수를
        반환한다. 정상 종료(SIGTERM) 시 만료를 기다리지 않고 즉시 다른
        프로세스가 획득할 수 있도록 한다(§6)."""
        ...
