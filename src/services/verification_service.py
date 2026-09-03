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

    async def _reject_if_backtest_failed(
        self, conn: asyncpg.pool.PoolConnectionProxy, listing_id: int
    ) -> None:
        """ADR-2026-09-04-C F-04/F-05, INVARIANTS.md I-07 — 자동 검증
        파이프라인(FND-04, `strategy_validation_result`)이 이미 FAIL로
        판정한 전략은 사람이 수동으로 승인할 수 없다. 이 게이트가 없으면
        `hard_fail_reasons`를 실제로 채우게 만든 F-04 수정(FAIL 판정 자체를
        가능하게 함)이 아무것도 강제하지 않는 장식으로 남는다."""
        row = await conn.fetchrow(
            "SELECT r.outcome, r.hard_fail_reasons "
            "FROM strategy_listings l "
            "JOIN strategy_validation_run run "
            "  ON run.strategy_id = l.strategy_id AND run.strategy_version = l.strategy_version "
            "JOIN strategy_validation_result r ON r.run_id = run.id "
            "WHERE l.id = $1 AND run.check_type = 'backtest' "
            "ORDER BY run.completed_at DESC NULLS LAST LIMIT 1",
            listing_id,
        )
        if row is not None and row["outcome"] == "FAIL":
            raise VerificationError(
                "이 전략의 자동 검증(backtest)이 FAIL입니다"
                f"(hard_fail_reasons={list(row['hard_fail_reasons'])}) — 승인할 수 없습니다."
            )

    async def decide(
        self,
        listing_id: int,
        verifier_id: UUID,
        decision: str,
        *,
        rejection_reason: str | None = None,
    ) -> VerificationResult:
        if decision not in VALID_DECISIONS:
            raise VerificationError(f"알 수 없는 결정입니다: {decision}")
        if decision == "REJECT" and not rejection_reason:
            raise VerificationError("REJECT 결정에는 사유가 필요합니다.")

        # 레드팀 감사(docs/RED_TEAM_FINDINGS.md #05) 반영 — "읽고 나서 별도로
        # 쓰기"였을 때는 서로 다른 두 검증담당자가 같은 리스팅을 거의 동시에
        # 하나는 승인, 하나는 반려하면 나중에 커밋되는 쪽이 조용히 덮어썼다.
        # UPDATE 자체에 status='PENDING_VERIFICATION' 조건을 걸어(payment_
        # confirmation_service.py::confirm_payment()와 동일 패턴) RETURNING이
        # 빈 행이면 그사이 다른 검증담당자가 먼저 처리했다는 뜻으로 실패시킨다.
        async with self._pool.acquire() as conn:
            pre_check = await conn.fetchrow(
                "SELECT status, seller_user_id FROM strategy_listings WHERE id = $1", listing_id
            )
            if pre_check is None:
                raise VerificationError("존재하지 않는 리스팅입니다.")
            if pre_check["status"] != "PENDING_VERIFICATION":
                raise VerificationError(
                    f"PENDING_VERIFICATION 상태에서만 검증할 수 있습니다"
                    f"(현재: {pre_check['status']})."
                )
            # 전수감사(docs/FULL_AUDIT_2026-09-02.md §2) 반영 — 15번 §15.6이
            # "API 레벨 강제"로 못박은 이해상충 규칙. 검증담당자가 자기 리스팅을
            # 승인하는 경로를 서비스 계층에서 닫는다(verification_queue_service의
            # 대기열 필터만으로는 listing_id를 직접 지정한 호출을 막지 못한다).
            if pre_check["seller_user_id"] == verifier_id:
                raise VerificationError("본인이 판매하는 리스팅은 검증할 수 없습니다(이해상충).")

            if decision == "APPROVE":
                await self._reject_if_backtest_failed(conn, listing_id)

            new_status = "LISTED" if decision == "APPROVE" else "DRAFT"
            if decision == "APPROVE":
                row = await conn.fetchrow(
                    "UPDATE strategy_listings SET status = $2, verified_at = now() "
                    "WHERE id = $1 AND status = 'PENDING_VERIFICATION' "
                    "RETURNING seller_user_id",
                    listing_id,
                    new_status,
                )
            else:
                # 레드팀 감사(#16) 반영 — 반려 사유를 UPDATE 자체에 저장한다.
                # 이전에는 검증만 하고 실제로 어디에도 남기지 않아 응답이
                # 나간 순간 완전히 사라졌다(판매자가 이유를 다시 알 수 없음).
                row = await conn.fetchrow(
                    "UPDATE strategy_listings SET status = $2, rejection_reason = $3 "
                    "WHERE id = $1 AND status = 'PENDING_VERIFICATION' "
                    "RETURNING seller_user_id",
                    listing_id,
                    new_status,
                    rejection_reason,
                )
            if row is None:
                raise VerificationError(
                    "이미 다른 검증담당자가 처리했습니다(동시 처리 충돌)."
                )

        if self._publish is not None:
            await self._publish(
                "strategy.verification.completed",
                {
                    "event_type": "strategy.verification.completed",
                    "user_id": str(row["seller_user_id"]),
                    "listing_id": listing_id,
                    "decision": decision,
                    "rejection_reason": rejection_reason if decision == "REJECT" else None,
                },
            )

        return VerificationResult(
            listing_id=listing_id,
            status=new_status,
            rejection_reason=rejection_reason if decision == "REJECT" else None,
        )
