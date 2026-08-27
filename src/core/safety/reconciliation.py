"""9.6 — Reconciliation 불일치 에스컬레이션.

Spec: 기능설계문서_v1.20.md#FD-9.6, 정책문서 8.4, 8.6-B

내부 상태와 거래소 실제 상태의 불일치를 감시하고 반복 시 단계적으로
대응을 격상한다:
  1회 불일치 → 자동 Recovery(재조회), 신규주문 일시보류(호출부 책임)
  동일자산 1시간 내 3회 이상 → Circuit Breaker RESTRICTED로 자동 승격
  24시간 내 5회 이상 또는 단일 불일치가 포지션의 10% 초과 → HALTED
  (신규주문 전면중지) — FD-9.4b 재가동 승인 워크플로를 그대로 재사용
  (CircuitBreakerService.force_escalate가 이미 그 인프라를 갖고 있음).

원인이 특정되지 않아 반복되더라도 자동으로 더 격상하지 않는다 —
force_escalate() 자체가 이미 그 이상이면 아무것도 하지 않는 멱등 동작이라
"자동화가 스스로 판단을 확대하지 않는다"(8.2-A 원칙)가 그대로 보장된다.
"""
from __future__ import annotations

import json
from decimal import Decimal
from typing import Any
from uuid import UUID

import asyncpg
from pydantic import BaseModel

from src.core.safety.circuit_breaker import CircuitBreakerLevel, CircuitBreakerService

HOURLY_REPEAT_THRESHOLD = 3
DAILY_REPEAT_THRESHOLD = 5
SEVERE_DISCREPANCY_PCT = Decimal("10")


class ReconciliationOutcome(BaseModel):
    event_id: int
    action: str  # "auto_recovery" | "circuit_breaker_restricted" | "full_halt_rca_required"
    count_1h: int
    count_24h: int


class ReconciliationService:
    def __init__(self, pool: asyncpg.Pool, circuit_breaker: CircuitBreakerService) -> None:
        self._pool = pool
        self._circuit_breaker = circuit_breaker

    async def record_and_escalate(
        self,
        *,
        user_id: UUID,
        symbol: str,
        exchange: str,
        internal_value: dict[str, Any],
        external_value: dict[str, Any],
        discrepancy_pct: Decimal | None = None,
        order_id: UUID | None = None,
        position_id: int | None = None,
    ) -> ReconciliationOutcome:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                INSERT INTO reconciliation_events
                    (user_id, symbol, exchange, order_id, position_id,
                     internal_value, external_value, discrepancy_pct)
                VALUES ($1, $2, $3, $4, $5, $6::jsonb, $7::jsonb, $8)
                RETURNING id
                """,
                user_id,
                symbol,
                exchange,
                order_id,
                position_id,
                json.dumps(internal_value),
                json.dumps(external_value),
                discrepancy_pct,
            )
        event_id = row["id"]

        count_1h = await self._count_since(symbol, exchange, hours=1)
        count_24h = await self._count_since(symbol, exchange, hours=24)
        severe_single = discrepancy_pct is not None and discrepancy_pct > SEVERE_DISCREPANCY_PCT

        if count_24h >= DAILY_REPEAT_THRESHOLD or severe_single:
            await self._circuit_breaker.force_escalate(
                CircuitBreakerLevel.HALTED, reason="reconciliation_severe_or_repeated_24h"
            )
            action = "full_halt_rca_required"
        elif count_1h >= HOURLY_REPEAT_THRESHOLD:
            await self._circuit_breaker.force_escalate(
                CircuitBreakerLevel.RESTRICTED, reason="reconciliation_repeated_1h"
            )
            action = "circuit_breaker_restricted"
        else:
            action = "auto_recovery"

        return ReconciliationOutcome(
            event_id=event_id, action=action, count_1h=count_1h, count_24h=count_24h
        )

    async def _count_since(self, symbol: str, exchange: str, *, hours: int) -> int:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT COUNT(*) AS cnt FROM reconciliation_events
                WHERE symbol = $1 AND exchange = $2
                    AND created_at >= now() - make_interval(hours => $3)
                """,
                symbol,
                exchange,
                hours,
            )
        count: int = row["cnt"]
        return count
