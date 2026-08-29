"""13.3 — 전략 검증 워크플로 (MVP 수동검증 브릿지).

Spec: 기능설계문서_v1.20.md#FD-13.2, 정책문서 9.5-A, v3.2

Black/Killer Team 자동화(Phase 4 의존)가 아직 없어 플랫폼 운영자가 동일
기준(오버피팅·Look-ahead Bias·Survivorship Bias 체크리스트)으로 수동
검증한다. 승인 시 PENDING_VERIFICATION → LISTED, 거부 시 → DRAFT(사유
기록)로 되돌린다.

완료조건(FD-13.2) — "검증 담당자 승인 없이는 LISTED로 전이 불가"는 이
서비스가 유일한 LISTED 전이 경로라는 애플리케이션 레벨 강제로 만족한다
(DB 트리거는 이 MVP 단계에서 과잉설계로 보류 — 다른 코드 경로 어디에도
status를 직접 LISTED로 바꾸는 곳이 없다).

APPROVE 시 verified_at도 함께 기록한다 — FD-13.8(검색·정렬)의 기본
정렬 기준(생성일이 아닌 검증통과일 역순, 재등록 조작 방지)이 이 값을
그대로 쓴다.

FD-17.1 이벤트 발행 — 검수 완료(승인/거부 모두) 시 리스팅 소유자에게
"strategy.verification.completed"를 발행한다.
"""
from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any
from uuid import UUID

import asyncpg
from pydantic import BaseModel

VALID_DECISIONS = ("APPROVE", "REJECT")

PublishFn = Callable[[str, dict[str, Any]], Awaitable[None]]


class VerificationError(Exception):
    """FD-13.2 실패 — 라우터가 400/404로 변환."""


class VerificationResult(BaseModel):
    listing_id: int
    status: str
    rejection_reason: str | None = None


class VerificationService:
    def __init__(self, pool: asyncpg.Pool, *, publish: PublishFn | None = None) -> None:
        self._pool = pool
        self._publish = publish

    async def decide(
        self,
        listing_id: int,
        verifier_id: UUID,  # noqa: ARG002 — 감사기록용, 저장 컬럼은 audit_log 연동 시 추가 예정
        decision: str,
        *,
        rejection_reason: str | None = None,
    ) -> VerificationResult:
        if decision not in VALID_DECISIONS:
            raise VerificationError(f"알 수 없는 결정입니다: {decision}")
        if decision == "REJECT" and not rejection_reason:
            raise VerificationError("REJECT 결정에는 사유가 필요합니다.")

        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT status, seller_user_id FROM strategy_listings WHERE id = $1", listing_id
            )
            if row is None:
                raise VerificationError("존재하지 않는 리스팅입니다.")
            if row["status"] != "PENDING_VERIFICATION":
                raise VerificationError(
                    f"PENDING_VERIFICATION 상태에서만 검증할 수 있습니다(현재: {row['status']})."
                )

            new_status = "LISTED" if decision == "APPROVE" else "DRAFT"
            if decision == "APPROVE":
                await conn.execute(
                    "UPDATE strategy_listings SET status = $2, verified_at = now() WHERE id = $1",
                    listing_id,
                    new_status,
                )
            else:
                await conn.execute(
                    "UPDATE strategy_listings SET status = $2 WHERE id = $1",
                    listing_id,
                    new_status,
                )

        if self._publish is not None:
            await self._publish(
                "strategy.verification.completed",
                {
                    "event_type": "strategy.verification.completed",
                    "user_id": str(row["seller_user_id"]),
                    "listing_id": listing_id,
                    "decision": decision,
                },
            )

        return VerificationResult(
            listing_id=listing_id,
            status=new_status,
            rejection_reason=rejection_reason if decision == "REJECT" else None,
        )
