"""M2-1 — `/ws/market` realtime market-data WS gateway.

Spec: docs/design/ADR-2026-09-09-B-mvp1-hardening-and-mvp2-scope.md M2-1.
Exposes DC-17 `application/realtime_fanout.py` (RealtimeFanout, already done)
as-is — entitlement/backpressure logic is not reimplemented here. This leaf
only adds three things: (1) connect-time JWT auth (same rule as the §3.4
per-request revoked_at check, applied once per connection), (2) a 50
concurrent-subscription cap per tenant (shared across that tenant's
connections), (3) the reconnect gap-backfill contract (the subscribe ack
carries a REST cursor hint — the actual REST call is the frontend hook's job,
M2-1b).

Auth: a FastAPI `Depends(Request)` chain happens to work on some WebSocket
scopes but is subtle across versions — here we verify directly before
`websocket.accept()` and fail with an explicit close code (4401) instead
(same kid-signature + active-session check as `get_current_user`, reproduced
inline).

Entitlement (PAPER MVP scope): the DC-9 `entitlements` table already has a
REALTIME/DELAYED `feed_type` column, but no adapter reads it yet (DC-8
migration decision: "an adapter for this table is out of this leaf's scope").
This gateway reuses REST's (`LA-24`) "registered venue" criterion for realtime
access — if a tenant can already see a venue over REST, a WS realtime stream
of the same venue is not additional exposure. Entitlement-tier granularity
(realtime vs delayed) is deferred to the future DC-9 grants adapter leaf (this
decision is itself that leaf's prerequisite).

Auth/subscription-cap/dispatch (`_pump`, etc.) actually live in
`market_ws_dispatch.py` — keeping them all in this file would hit the
repo-wide architecture guard (P6.line_cap, 300 lines). This module imports
those functions as-is and wires up only the one handler (the names tests use
— `_pump`/`_feed_from_message`/`_json_default`/`_TenantSubscriptionLimiter`/
`reset_gateway_state_for_test` — are still visible on this module's namespace,
since importing a name makes it a module attribute).
"""

from __future__ import annotations

import asyncio
from uuid import UUID

import asyncpg
from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from src.api.ws.market_ws_dispatch import (
    MAX_SUBSCRIPTIONS_PER_TENANT,
    TenantSubscriptionLimiter,
    build_subject,
    feed_from_message,
    get_realtime_fanout,
    get_subscription_limiter,
    json_default,
    pump,
    reset_gateway_state_for_test,
    resume_hint,
    send,
)
from src.api.ws.market_ws_dispatch import authenticate as _authenticate

# Tests reach these via `market_ws._pump` / `market_ws._TenantSubscriptionLimiter` /
# `market_ws._feed_from_message` / `market_ws._json_default` — re-exported
# under the names this module used before the split.
_pump = pump
_send = send
_TenantSubscriptionLimiter = TenantSubscriptionLimiter
_feed_from_message = feed_from_message
_json_default = json_default
_build_subject = build_subject
_resume_hint = resume_hint

__all__ = [
    "router",
    "get_realtime_fanout",
    "get_subscription_limiter",
    "reset_gateway_state_for_test",
]

router = APIRouter()

_AUTH_CLOSE_CODE = 4401


@router.websocket("/ws/market")
async def market_data_ws(websocket: WebSocket) -> None:
    identity = await _authenticate(websocket)
    if identity is None:
        await websocket.close(code=_AUTH_CLOSE_CODE)
        return
    tenant_id, subject_id = identity

    await websocket.accept()
    pool: asyncpg.Pool = websocket.app.state.pool
    fanout = get_realtime_fanout()
    limiter = get_subscription_limiter()
    pumps: dict[UUID, asyncio.Task[None]] = {}

    async def cleanup() -> None:
        tasks = list(pumps.values())
        for sub_id in pumps:
            fanout.unsubscribe(sub_id)
            limiter.release(tenant_id)
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        pumps.clear()

    try:
        while True:
            try:
                message = await websocket.receive_json()
            except WebSocketDisconnect:
                break
            except (ValueError, TypeError):
                await send(websocket, {"op": "error", "message": "invalid JSON"})
                continue

            if not isinstance(message, dict):
                await send(websocket, {"op": "error", "message": "message must be an object"})
                continue

            op = message.get("op")
            if op == "subscribe":
                try:
                    feed = feed_from_message(message)
                except (KeyError, ValueError) as exc:
                    await send(websocket, {"op": "error", "message": str(exc)})
                    continue
                if not limiter.try_acquire(tenant_id):
                    await send(
                        websocket,
                        {
                            "op": "error",
                            "code": "SUBSCRIPTION_LIMIT_EXCEEDED",
                            "message": (
                                f"tenant subscription cap ({MAX_SUBSCRIPTIONS_PER_TENANT}) exceeded"
                            ),
                        },
                    )
                    continue
                subject = await build_subject(
                    pool, tenant_id=tenant_id, subject_id=subject_id, feed=feed
                )
                subscription = fanout.subscribe(subject, feed)
                pumps[subscription.subscription_id] = asyncio.create_task(
                    pump(websocket, subscription.subscription_id, subscription.queue)
                )
                await send(
                    websocket,
                    {
                        "op": "subscribed",
                        "subscription_id": str(subscription.subscription_id),
                        "resume": resume_hint(feed),
                    },
                )
            elif op == "unsubscribe":
                try:
                    sub_id = UUID(str(message.get("subscription_id")))
                except ValueError:
                    await send(websocket, {"op": "error", "message": "invalid subscription_id"})
                    continue
                task = pumps.pop(sub_id, None)
                if task is not None:
                    task.cancel()
                    fanout.unsubscribe(sub_id)
                    limiter.release(tenant_id)
                await send(websocket, {"op": "unsubscribed", "subscription_id": str(sub_id)})
            else:
                await send(websocket, {"op": "error", "message": f"unknown op {op!r}"})
    finally:
        await cleanup()
