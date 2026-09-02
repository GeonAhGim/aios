"""FD-14(신설) — 가격/지표 알림 (AlertService).

Spec: 사용자 요청(2026-09-01) — "가격/지표 알림" 기능. 조건 스키마는
condition_compiler.py/preview_service.py가 이미 쓰는 지표+연산자+임계값
계약을 그대로 재사용한다(condition_evaluation.py::compare_value 공유).

편차: 이 시스템에는 아직 백그라운드 스케줄러가 없다(main.py의
heartbeat 루프가 유일한 선례) — evaluate_all_active()를 그 루프와 같은
패턴(주기적 asyncio.sleep 루프, main.py lifespan)으로 호출한다. 알림
평가는 사용자별 거래소 자격증명을 통해 캔들을 가져오는데, 자격증명이
해지됐거나 일시적으로 조회에 실패해도 그 알림 하나만 이번 주기에
건너뛰고 다음 주기에 재시도한다 — 다른 사용자의 알림 평가를 막으면 안
되므로 루프 전체를 실패시키지 않는다(보안/금전 이벤트가 아니라 감사
로그 대상은 아님).

알림이 발동하면 FD-17(알림 게이트웨이)의 "alert.triggered" 이벤트로
발행한다 — 실제 이메일/푸시 발송기가 아직 없어(다른 FD-17 이벤트와 동일)
발송 자체는 여전히 "실패"로 정직하게 기록되지만, 그 발동 사실은
triggered_at/triggered_value로 DB에 남아 사용자가 알림 목록 화면에서
확인할 수 있다.
"""
from __future__ import annotations

import json
import logging
from collections.abc import Awaitable, Callable
from datetime import datetime
from typing import Any
from uuid import UUID

import asyncpg
from pydantic import BaseModel

from src.core.indicators.talib_adapter import IndicatorService
from src.services.condition_evaluation import Operator, compare_value
from src.services.credential_resolver import CredentialNotFoundError, CredentialResolver

DEFAULT_CANDLE_LIMIT = 200
# 레드팀 #24 — 사용자당 ACTIVE 알림 상한. evaluate_all_active()가 전체
# 알림을 순차 for 루프로 도는 구조라, 한 사용자가 대량 생성하면 그
# 사용자 몫만큼 매 평가 주기의 처리 시간이 늘어나 다른 모든 사용자의
# 평가도 함께 지연된다 — Draft 값(정책 문서에 정식 근거는 아직 없음,
# DoS 방지 목적의 안전한 상한).
MAX_ACTIVE_ALERTS_PER_USER = 50

logger = logging.getLogger(__name__)

PublishFn = Callable[[str, dict[str, Any]], Awaitable[None]]


class AlertError(Exception):
    """FD-14 실패 — 라우터가 400/404로 변환."""


class PriceAlert(BaseModel):
    id: int
    user_id: UUID
    exchange: str
    symbol: str
    timeframe: str
    indicator: str
    params: dict[str, int]
    operator: str
    threshold: float
    status: str
    created_at: datetime
    triggered_at: datetime | None
    triggered_value: float | None


def _row_to_alert(row: asyncpg.Record) -> PriceAlert:
    data = dict(row)
    data["params"] = json.loads(data["params"])
    return PriceAlert(**data)


class AlertService:
    def __init__(
        self,
        pool: asyncpg.Pool,
        *,
        credential_resolver: CredentialResolver,
        indicator_service: IndicatorService | None = None,
        publish: PublishFn | None = None,
    ) -> None:
        self._pool = pool
        self._resolver = credential_resolver
        self._indicators = indicator_service or IndicatorService()
        self._publish = publish

    async def create_alert(
        self,
        user_id: UUID,
        *,
        exchange: str,
        symbol: str,
        timeframe: str,
        indicator: str,
        params: dict[str, int],
        operator: Operator,
        threshold: float,
    ) -> PriceAlert:
        async with self._pool.acquire() as conn:
            active_count = await conn.fetchval(
                "SELECT COUNT(*) FROM price_alerts WHERE user_id = $1 AND status = 'ACTIVE'",
                user_id,
            )
            if active_count >= MAX_ACTIVE_ALERTS_PER_USER:
                raise AlertError(
                    f"활성 알림 상한({MAX_ACTIVE_ALERTS_PER_USER}개)에 도달했습니다 — "
                    "기존 알림을 취소한 뒤 다시 시도하세요."
                )
            row = await conn.fetchrow(
                """
                INSERT INTO price_alerts
                    (user_id, exchange, symbol, timeframe, indicator, params, operator, threshold)
                VALUES ($1, $2, $3, $4, $5, $6::jsonb, $7, $8)
                RETURNING *
                """,
                user_id,
                exchange,
                symbol,
                timeframe,
                indicator,
                json.dumps(params),
                operator,
                threshold,
            )
        return _row_to_alert(row)

    async def list_my_alerts(self, user_id: UUID) -> list[PriceAlert]:
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT * FROM price_alerts WHERE user_id = $1 ORDER BY created_at DESC",
                user_id,
            )
        return [_row_to_alert(row) for row in rows]

    async def cancel_alert(self, user_id: UUID, alert_id: int) -> PriceAlert:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                "UPDATE price_alerts SET status = 'CANCELLED' "
                "WHERE id = $1 AND user_id = $2 AND status = 'ACTIVE' RETURNING *",
                alert_id,
                user_id,
            )
        if row is None:
            raise AlertError("취소할 수 있는 활성 알림을 찾을 수 없습니다.")
        return _row_to_alert(row)

    async def evaluate_all_active(self) -> list[PriceAlert]:
        """활성 알림을 전부 순회해 조건이 충족된 것만 TRIGGERED로 전이시키고
        반환한다. 자격증명 미등록 등 사용자별 일시적 실패는 그 알림만
        건너뛴다(모듈 docstring 참조)."""
        async with self._pool.acquire() as conn:
            rows = await conn.fetch("SELECT * FROM price_alerts WHERE status = 'ACTIVE'")
        alerts = [_row_to_alert(row) for row in rows]

        triggered: list[PriceAlert] = []
        for alert in alerts:
            try:
                adapter = await self._resolver.get_adapter(alert.user_id, alert.exchange)
                candles = await adapter.get_ohlcv(
                    alert.symbol, alert.timeframe, limit=DEFAULT_CANDLE_LIMIT
                )
            except CredentialNotFoundError:
                continue

            # 레드팀 #2026-09-02-21 — 미검증 indicator/params가 여기서 예외를
            # 던지면(IndicatorError/TypeError 등) 이 알림 하나만 건너뛰어야
            # 한다(위 docstring 약속) — 원래는 이 호출이 try/except 밖에 있어
            # 예외가 루프(그리고 그 루프를 감싼 백그라운드 태스크)를 통째로
            # 죽여 전체 사용자의 알림 평가가 영구 정지했다.
            try:
                result = self._indicators.calculate(alert.indicator, candles, **alert.params)
            except Exception:
                logger.warning(
                    "alert_id=%s의 indicator=%r/params=%r 계산 실패 — 이 알림만 건너뜁니다.",
                    alert.id,
                    alert.indicator,
                    alert.params,
                    exc_info=True,
                )
                continue
            if not result.values:
                continue
            value = result.values[-1]
            if value is None:
                continue
            prev_value = result.values[-2] if len(result.values) >= 2 else None

            if not compare_value(value, alert.operator, alert.threshold, prev_value):
                continue

            async with self._pool.acquire() as conn:
                row = await conn.fetchrow(
                    "UPDATE price_alerts SET status = 'TRIGGERED', triggered_at = now(), "
                    "triggered_value = $2 WHERE id = $1 AND status = 'ACTIVE' RETURNING *",
                    alert.id,
                    value,
                )
            if row is None:
                continue  # 동시에 취소되는 등 이미 다른 경로가 상태를 바꿈
            updated = _row_to_alert(row)
            triggered.append(updated)

            if self._publish is not None:
                await self._publish(
                    "alert.triggered",
                    {
                        "event_type": "alert.triggered",
                        "user_id": str(alert.user_id),
                        "alert_id": alert.id,
                        "symbol": alert.symbol,
                        "indicator": alert.indicator,
                        "operator": alert.operator,
                        "threshold": alert.threshold,
                        "triggered_value": value,
                    },
                )

        return triggered
