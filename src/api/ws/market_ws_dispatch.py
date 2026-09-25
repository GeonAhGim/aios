"""M2-1 — auth/subscription-cap/dispatch support functions for the
`/ws/market` gateway.

Holds only the pure support layer the `@router.websocket` handler in
`market_ws.py` uses (no router registration here) — split out of the handler
file because of the repo-wide architecture guard (P6.line_cap, 300 lines).
The rationale for each function follows `market_ws.py`'s module docstring
(one-time auth check, 50 subscriptions per tenant, reconnect-gap REST
contract).
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

import asyncpg
from fastapi import WebSocket
from pydantic import BaseModel

from src.api.deps import get_token_verifier
from src.core.observability import metrics as metrics_module
from src.data.models.base import AssetClass
from src.foundation.market_data.adapters.postgres_tenant_venues import PostgresTenantVenueSource
from src.foundation.market_data.application.realtime_fanout import RealtimeFanout
from src.foundation.market_data.contracts.v1 import Timeframe, Venue
from src.foundation.market_data.domain.entitlement.policy import (
    EntitlementGrant,
    EntitlementSubject,
    FeedRequest,
)
from src.foundation.market_data.ports.entitlement import VenueRegistrySource
from src.services.auth import session_repository
from src.services.auth.tokens import TokenInvalidError
from src.services.auth_service import get_user_by_id

MAX_SUBSCRIPTIONS_PER_TENANT = 50
GAP_BACKFILL_REST_PATH = "/v1/foundation/market-data/candles"


class TenantSubscriptionLimiter:
    """Concurrent-subscription cap per tenant — a counter shared across that
    tenant's WS connections (the tenant, not the connection, is the unit of
    the cap, per the spec wording "50 subscriptions per tenant")."""

    def __init__(self, max_per_tenant: int) -> None:
        self._max = max_per_tenant
        self._counts: dict[UUID, int] = {}

    def try_acquire(self, tenant_id: UUID) -> bool:
        count = self._counts.get(tenant_id, 0)
        if count >= self._max:
            return False
        self._counts[tenant_id] = count + 1
        return True

    def release(self, tenant_id: UUID) -> None:
        count = self._counts.get(tenant_id, 0)
        if count <= 1:
            self._counts.pop(tenant_id, None)
        else:
            self._counts[tenant_id] = count - 1

    def reset_for_test(self) -> None:
        self._counts.clear()


_fanout = RealtimeFanout(metrics=metrics_module.metrics())
_limiter = TenantSubscriptionLimiter(MAX_SUBSCRIPTIONS_PER_TENANT)


def get_realtime_fanout() -> RealtimeFanout:
    return _fanout


def get_subscription_limiter() -> TenantSubscriptionLimiter:
    return _limiter


def reset_gateway_state_for_test() -> None:
    """Test-only — resets the process singletons (_fanout/_limiter) so
    subscription counts don't leak across tests (same override pattern as
    `metrics.py::set_metrics`)."""
    global _fanout
    _fanout = RealtimeFanout(metrics=metrics_module.metrics())
    _limiter.reset_for_test()


async def authenticate(websocket: WebSocket) -> tuple[UUID, UUID] | None:
    """Verifies the token and checks the session is active. Returns `None` on
    failure (the caller closes the socket) — applies the same rule as
    `src/api/deps.py::get_current_user`, once at connect time (re-verifying
    mid-connection / propagating a forced revoke is out of this leaf's
    scope)."""
    token = websocket.query_params.get("token")
    if not token:
        return None
    pool: asyncpg.Pool = websocket.app.state.pool
    verifier = get_token_verifier()
    try:
        claims = verifier.verify(token)
    except TokenInvalidError:
        return None

    user = await get_user_by_id(pool, claims.sub)
    if user is None or user.status in ("SUSPENDED", "DELETED"):
        return None

    async with pool.acquire() as conn:
        session = await session_repository.get_active(conn, claims.sid)
    if session is None:
        return None

    return claims.tid, claims.sub


async def build_subject(
    pool: asyncpg.Pool, *, tenant_id: UUID, subject_id: UUID, feed: FeedRequest
) -> EntitlementSubject:
    source: VenueRegistrySource = PostgresTenantVenueSource(pool)
    venues = await source.registered_venues(tenant_id)
    if feed.venue not in venues:
        return EntitlementSubject(tenant_id=tenant_id, subject_id=subject_id, grants=())
    grant = EntitlementGrant(
        tenant_id=tenant_id,
        subject_id=subject_id,
        venue=feed.venue,
        asset_class=feed.asset_class,
        instrument_ids=None,
        timeframes=frozenset({feed.timeframe}),
        realtime=True,
        delayed_seconds=0,
        expires_at=None,
    )
    return EntitlementSubject(tenant_id=tenant_id, subject_id=subject_id, grants=(grant,))


def feed_from_message(message: dict[str, Any]) -> FeedRequest:
    return FeedRequest(
        venue=Venue(message["venue"]),
        asset_class=AssetClass(message["asset_class"]),
        instrument_id=str(message["instrument_id"]),
        timeframe=Timeframe(message["timeframe"]),
        want_realtime=True,
    )


def json_default(value: Any) -> Any:
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    raise TypeError(f"not JSON serializable: {type(value)!r}")


async def send(websocket: WebSocket, message: dict[str, Any]) -> None:
    await websocket.send_text(json.dumps(message, default=json_default))


async def pump(websocket: WebSocket, subscription_id: UUID, queue: asyncio.Queue[Any]) -> None:
    """Subscription queue -> socket. Ends quietly on cancellation
    (disconnect/unsubscribe). If the socket send fails (e.g. the client
    already dropped but the main loop doesn't know yet), the exception stays
    inside this task and never touches the process — the main loop eventually
    detects the disconnect on its next `receive_json()` and cleans up."""
    try:
        while True:
            envelope = await queue.get()
            payload = envelope.payload
            if isinstance(payload, BaseModel):
                payload = payload.model_dump(mode="json")
            await send(
                websocket,
                {
                    "op": "candle",
                    "subscription_id": str(subscription_id),
                    "topic": envelope.topic,
                    "trace_id": str(envelope.trace_id),
                    "occurred_at": envelope.occurred_at.isoformat(),
                    "payload": payload,
                },
            )
    except asyncio.CancelledError:
        pass


def resume_hint(feed: FeedRequest) -> dict[str, Any]:
    """Reconnect gap-backfill contract — carries how to fill in everything
    after the last received candle via REST, in the subscribe ack. The actual
    REST call is the frontend hook's job (M2-1b)."""
    return {
        "rest_endpoint": GAP_BACKFILL_REST_PATH,
        "query": {
            "venue": feed.venue.value,
            "timeframe": feed.timeframe.value,
            # `FeedRequest.instrument_id` is a venue symbol string (not the
            # REST UUID `instrument_id` path param) — the REST `/candles`
            # `symbol` query param is the one it maps onto directly.
            "symbol": feed.instrument_id,
        },
        "cursor_param": "start",
        "instructions": (
            "On reconnect, call the candles REST endpoint with `start` set to "
            "the close_time of the last received candle payload to backfill "
            "the gap."
        ),
    }
