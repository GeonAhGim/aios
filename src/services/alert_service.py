"""FD-14 (new) — Price/indicator alerts (AlertService).

Spec: User request (2026-09-01) — "price/indicator alert" feature. The condition
schema reuses the indicator+operator+threshold contract already in use by
condition_compiler.py/preview_service.py (shares condition_evaluation.py::compare_value).

Deviation: This system has no background scheduler yet (main.py's
heartbeat loop is the only precedent) — call evaluate_all_active() in the same
pattern as that loop (periodic asyncio.sleep loop, main.py lifespan). Alert
evaluation fetches candles via per-user exchange credentials; if a credential
has been revoked or temporarily fails to query, only that one alert is skipped
this cycle and retried next cycle — the loop must not fail entirely, because
other users' alert evaluations must not be blocked (not a security/monetary
event, not an audit-log target).

When an alert fires, it publishes an "alert.triggered" event to FD-17 (alert
gateway) — since the actual email/push sender is not yet implemented (same as
other FD-17 events), the send itself is honestly recorded as "failed", but the
fire fact remains in the DB via triggered_at/triggered_value so users can see
it on the alert list screen.
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
# Red team #24 — Max ACTIVE alerts per user. Since evaluate_all_active() iterates
# all alerts in a sequential for loop, if one user creates a large number, their
# share increases processing time each evaluation cycle and delays evaluation for
# all other users — a safe upper limit (Draft value; no formal policy doc basis yet,
# DoS prevention purpose).
MAX_ACTIVE_ALERTS_PER_USER = 50

logger = logging.getLogger(__name__)

PublishFn = Callable[[str, dict[str, Any]], Awaitable[None]]


class AlertError(Exception):
    """FD-14 creation failure (active alert limit reached, etc.) — VALIDATION_INVALID_FIELD(400)."""


class AlertNotFoundError(AlertError):
    """No active alert to cancel — kept as a separate subclass so it maps to
    RESOURCE_NOT_FOUND(404) (exception_mapping.py EXCEPTION_MAP is type-based,
    so the same class can only map to one status code — same practice as
    PLT-17's ExchangeCredentialNotFoundError)."""


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
            raise AlertNotFoundError("취소할 수 있는 활성 알림을 찾을 수 없습니다.")
        return _row_to_alert(row)

    async def evaluate_all_active(self) -> list[PriceAlert]:
        """Iterate all active alerts, transition only those whose conditions are met
        to TRIGGERED, and return them. Per-user transient failures such as
        unregistered credentials skip only that alert (see module docstring)."""
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

            # Red team #2026-09-02-21 — If unverified indicator/params throw an
            # exception here (IndicatorError/TypeError, etc.), only this one alert
            # should be skipped (promise in the docstring above) — originally this
            # call was outside try/except, so an exception would kill the entire
            # loop (and the background task wrapping it), permanently freezing
            # alert evaluation for all users.
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
                continue  # another path already changed the state (e.g. concurrently cancelled)
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
