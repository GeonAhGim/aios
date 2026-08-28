"""10.2 — SURGE 모드 판정 및 배치승인.

Spec: 기능설계문서_v1.20.md#FD-10.2, 정책문서 4.9-A, 6·7차 레드팀

단일 시장 이벤트로 승인 요청이 폭증할 때, 승인권자가 개별 검토에 매몰되지
않도록 검증된 단일 이벤트 파생 요청군만 배치승인한다. Trigger Provenance
태그가 시스템 실제 상태와 대조해 근거를 찾을 수 없으면(위조·오류 의심)
배치승인 대상에서 제외하고 개별 절차로 격하한다 — Agent 자기주장을
그대로 믿지 않는다(7차 레드팀 원칙).
"""
from __future__ import annotations

from collections.abc import Awaitable, Callable
from decimal import Decimal
from uuid import UUID

import asyncpg
from pydantic import BaseModel

from src.core.approval import service as approval

SURGE_MULTIPLIER = Decimal("5")
SURGE_WINDOW_MINUTES = 10
BASELINE_WINDOW_HOURS = 24
MIN_BASELINE_FLOOR = 3  # 평소 요청이 0에 가까울 때 사소한 증가로 오탐하지 않기 위한 하한

VerifyProvenanceFn = Callable[[str], Awaitable[bool]]


class SurgeClassification(BaseModel):
    is_surging: bool
    batch_eligible_ids: list[int]
    individual_review_ids: list[int]


class SurgeDetector:
    def __init__(
        self,
        pool: asyncpg.Pool,
        *,
        surge_multiplier: Decimal = SURGE_MULTIPLIER,
        window_minutes: int = SURGE_WINDOW_MINUTES,
        baseline_window_hours: int = BASELINE_WINDOW_HOURS,
    ) -> None:
        self._pool = pool
        self._surge_multiplier = surge_multiplier
        self._window_minutes = window_minutes
        self._baseline_window_hours = baseline_window_hours

    async def is_surging(self, *, trigger_source: str | None = None) -> bool:
        recent_count = await self._count_since_minutes(self._window_minutes, trigger_source)
        baseline_total = await self._count_since_minutes(
            self._baseline_window_hours * 60, trigger_source
        )
        # 최근 구간을 제외한 나머지 기간의 "구간당 평균" — 현재 급증분이
        # 스스로를 부풀리지 않도록 분리.
        remaining_minutes = self._baseline_window_hours * 60 - self._window_minutes
        remaining_count = max(baseline_total - recent_count, 0)
        num_windows = max(remaining_minutes // self._window_minutes, 1)
        baseline_rate = Decimal(remaining_count) / Decimal(num_windows)
        baseline_rate = max(baseline_rate, Decimal(MIN_BASELINE_FLOOR))

        return Decimal(recent_count) >= baseline_rate * self._surge_multiplier

    async def classify_for_batch_approval(
        self, *, verify_provenance: VerifyProvenanceFn, trigger_source: str | None = None
    ) -> SurgeClassification:
        surging = await self.is_surging(trigger_source=trigger_source)
        pending = await self._fetch_recent_pending(trigger_source)

        batch_eligible: list[int] = []
        individual_review: list[int] = []
        if not surging:
            return SurgeClassification(
                is_surging=False,
                batch_eligible_ids=[],
                individual_review_ids=[r["id"] for r in pending],
            )

        for request in pending:
            provenance = request["provenance"]
            if provenance is None:
                individual_review.append(request["id"])
                continue
            try:
                verified = await verify_provenance(provenance)
            except Exception:  # noqa: BLE001 — 검증 실패도 "근거 못 찾음"과 동일 취급
                verified = False
            if verified:
                batch_eligible.append(request["id"])
            else:
                individual_review.append(request["id"])

        return SurgeClassification(
            is_surging=True,
            batch_eligible_ids=batch_eligible,
            individual_review_ids=individual_review,
        )

    async def batch_approve(self, request_ids: list[int], approver_id: UUID) -> list[int]:
        """검증된 배치승인 대상만 일괄 승인한다. 실패한 건은 조용히 넘기지
        않고 결과에서 제외해 호출부가 알 수 있게 한다."""
        approved: list[int] = []
        for request_id in request_ids:
            try:
                result = await approval.approve(self._pool, request_id, approver_id)
                if result.status == "APPROVED":
                    approved.append(request_id)
            except approval.ApprovalError:
                continue
        return approved

    async def _count_since_minutes(self, minutes: int, trigger_source: str | None) -> int:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT COUNT(*) AS cnt FROM approval_requests "
                "WHERE created_at >= now() - make_interval(mins => $1) "
                "AND ($2::text IS NULL OR trigger_source = $2)",
                minutes,
                trigger_source,
            )
        count: int = row["cnt"]
        return count

    async def _fetch_recent_pending(self, trigger_source: str | None) -> list[asyncpg.Record]:
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT id, provenance FROM approval_requests "
                "WHERE status = 'PENDING' AND created_at >= now() - make_interval(mins => $1) "
                "AND ($2::text IS NULL OR trigger_source = $2)",
                self._window_minutes,
                trigger_source,
            )
        return list(rows)
