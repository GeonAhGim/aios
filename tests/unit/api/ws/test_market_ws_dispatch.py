"""M2-1 — `src/api/ws/market_ws.py` 디스패치 계층 단위 테스트(DB/네트워크 없음).

Spec: docs/design/ADR-2026-09-09-B-mvp1-hardening-and-mvp2-scope.md M2-1.
DoD 중 "부하 테스트(구독 500, p95 500ms 이하 로컬)"는 여기서 검증한다 —
실제 WS 소켓·인증·DB 없이 게이트웨이가 실제로 쓰는 `_pump` 코루틴 + DC-17
`RealtimeFanout`을 그대로 구동해 발행 1건이 500개 구독자 큐를 거쳐 "소켓"
전송까지 도달하는 지연을 잰다(가짜 소켓은 `send_text` 호출 시각만 기록).
"""

from __future__ import annotations

import asyncio
import time
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Any
from uuid import UUID, uuid4

import pytest

from src.api.ws import market_ws, market_ws_dispatch
from src.core.observability.metrics import NullMetrics
from src.data.models.base import AssetClass
from src.foundation.market_data.application.realtime_fanout import RealtimeFanout
from src.foundation.market_data.contracts.v1 import Timeframe, Venue
from src.foundation.market_data.domain.entitlement.policy import (
    EntitlementGrant,
    EntitlementSubject,
    FeedRequest,
)
from src.services.auth import session_repository
from src.services.auth.tokens import AccessClaims, AuthLevel
from src.services.auth_service import User

_TENANT = UUID("22222222-2222-2222-2222-222222222222")
_SUBJECT = UUID("44444444-4444-4444-4444-444444444444")
_NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)


class _FakeWebSocket:
    def __init__(self, *, fail_first: bool = False) -> None:
        self.sent: list[str] = []
        self.received_at: list[float] = []
        self._fail_first = fail_first

    async def send_text(self, data: str) -> None:
        if self._fail_first:
            self._fail_first = False
            raise RuntimeError("client already disconnected (simulated)")
        self.sent.append(data)
        self.received_at.append(time.perf_counter())


def _feed(**overrides: Any) -> FeedRequest:
    fields: dict[str, Any] = dict(
        venue=Venue.BITGET,
        asset_class=AssetClass.CRYPTO,
        instrument_id="BTC-USDT",
        timeframe=Timeframe.M1,
        want_realtime=True,
    )
    fields.update(overrides)
    return FeedRequest(**fields)


def _subject(feed: FeedRequest) -> EntitlementSubject:
    grant = EntitlementGrant(
        tenant_id=_TENANT,
        subject_id=_SUBJECT,
        venue=feed.venue,
        asset_class=feed.asset_class,
        instrument_ids=None,
        timeframes=frozenset({feed.timeframe}),
        realtime=True,
        delayed_seconds=0,
        expires_at=None,
    )
    return EntitlementSubject(tenant_id=_TENANT, subject_id=_SUBJECT, grants=(grant,))


@pytest.mark.perf
async def test_load_500_subscribers_p95_latency_within_500ms() -> None:
    fanout = RealtimeFanout(metrics=NullMetrics())
    feed = _feed()
    subject = _subject(feed)

    sockets: list[_FakeWebSocket] = []
    tasks: list[asyncio.Task[None]] = []
    for _ in range(500):
        subscription = fanout.subscribe(subject, feed)
        ws = _FakeWebSocket()
        sockets.append(ws)
        tasks.append(
            asyncio.create_task(
                market_ws._pump(ws, subscription.subscription_id, subscription.queue)
            )
        )

    try:
        start = time.perf_counter()
        await fanout.publish(feed, {"px": "100.5"}, as_of=_NOW)

        deadline = start + 2.0
        while time.perf_counter() < deadline and any(not s.received_at for s in sockets):
            await asyncio.sleep(0.001)
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

    latencies = sorted(s.received_at[0] - start for s in sockets if s.received_at)
    assert len(latencies) == 500, "500개 구독자 전원이 발행 1건을 수신해야 한다"
    p95_index = max(0, int(round(0.95 * len(latencies))) - 1)
    p95_seconds = latencies[p95_index]
    assert p95_seconds <= 0.5, f"p95={p95_seconds * 1000:.1f}ms > 500ms 예산 초과"


async def test_pump_send_failure_does_not_corrupt_sibling_subscribers() -> None:
    """실패주입 — 한 구독자의 소켓 송신이 실패해도(클라이언트가 이미 끊긴
    경우를 시뮬레이션) 다른 구독자는 정상 수신하고, 실패한 태스크는 예외를
    들고 조용히 끝난다(프로세스 전체를 건드리지 않는다)."""
    fanout = RealtimeFanout(metrics=NullMetrics())
    feed = _feed()
    subject = _subject(feed)

    failing_sub = fanout.subscribe(subject, feed)
    healthy_sub = fanout.subscribe(subject, feed)
    failing_ws = _FakeWebSocket(fail_first=True)
    healthy_ws = _FakeWebSocket()

    failing_task = asyncio.create_task(
        market_ws._pump(failing_ws, failing_sub.subscription_id, failing_sub.queue)
    )
    healthy_task = asyncio.create_task(
        market_ws._pump(healthy_ws, healthy_sub.subscription_id, healthy_sub.queue)
    )

    await fanout.publish(feed, {"px": "1"}, as_of=_NOW)
    await asyncio.sleep(0.05)

    assert healthy_ws.sent, "건강한 구독자는 발행분을 그대로 받아야 한다"
    assert failing_ws.sent == [], "실패한 구독자는 송신이 안 됐다"
    assert failing_task.done()
    with pytest.raises(RuntimeError, match="disconnected"):
        failing_task.result()
    assert not healthy_task.done()

    healthy_task.cancel()
    await asyncio.gather(healthy_task, return_exceptions=True)


def test_tenant_subscription_limiter_enforces_cap_and_release() -> None:
    limiter = market_ws._TenantSubscriptionLimiter(2)
    tenant = uuid4()
    assert limiter.try_acquire(tenant) is True
    assert limiter.try_acquire(tenant) is True
    assert limiter.try_acquire(tenant) is False, "상한(2) 초과는 거부돼야 한다"

    limiter.release(tenant)
    assert limiter.try_acquire(tenant) is True, "release 후에는 다시 확보할 수 있어야 한다"
    assert limiter.try_acquire(tenant) is False

    other_tenant = uuid4()
    assert limiter.try_acquire(other_tenant) is True, "상한은 테넌트별로 독립적이어야 한다"


def test_feed_from_message_rejects_unknown_venue() -> None:
    with pytest.raises(ValueError):
        market_ws._feed_from_message(
            {
                "venue": "NOT_A_REAL_VENUE",
                "asset_class": "CRYPTO",
                "instrument_id": "BTC-USDT",
                "timeframe": "1m",
            }
        )


def test_feed_from_message_rejects_missing_field() -> None:
    with pytest.raises(KeyError):
        market_ws._feed_from_message({"venue": "BITGET", "asset_class": "CRYPTO"})


def test_json_default_rejects_unserializable_type() -> None:
    class _Opaque:
        pass

    with pytest.raises(TypeError, match="not JSON serializable"):
        market_ws._json_default(_Opaque())


class _FakeAcquireCtx:
    async def __aenter__(self) -> None:
        return None

    async def __aexit__(self, *exc_info: Any) -> bool:
        return False


class _FakePool:
    """DB/네트워크 없이 `authenticate()`의 `async with pool.acquire()` 패턴만
    충족하는 가짜 풀 — `session_repository.get_active`는 monkeypatch로 대체되어
    실제 conn을 쓰지 않는다."""

    def acquire(self) -> _FakeAcquireCtx:
        return _FakeAcquireCtx()


class _AuthFakeWebSocket:
    def __init__(self, *, token: str | None, pool: _FakePool) -> None:
        self.query_params: dict[str, str] = {} if token is None else {"token": token}
        self.app = SimpleNamespace(state=SimpleNamespace(pool=pool))


def _claims(
    *, user_id: UUID, tenant_id: UUID, session_id: UUID, auth_level: AuthLevel
) -> AccessClaims:
    now = int(time.time())
    return AccessClaims(
        sub=user_id,
        tid=tenant_id,
        sid=session_id,
        jti=uuid4(),
        iat=now,
        exp=now + 900,
        nbf=now,
        auth_level=auth_level,
    )


def _active_user(user_id: UUID) -> User:
    return User(
        user_id=user_id,
        email="owner@example.com",
        display_name=None,
        mfa_enabled=False,
        mfa_verified_at=None,
        status="ACTIVE",
        is_verifier=False,
        is_platform_admin=False,
    )


def _active_session(
    *, session_id: UUID, user_id: UUID, tenant_id: UUID, auth_level: AuthLevel
) -> session_repository.Session:
    now = datetime.now(timezone.utc)
    return session_repository.Session(
        id=session_id,
        user_id=user_id,
        tenant_id=tenant_id,
        refresh_hash="deadbeef",
        auth_level=auth_level,
        issued_at=now,
        rotated_at=None,
        expires_at=now + timedelta(days=14),
        revoked_at=None,
        revoke_reason=None,
    )


async def test_authenticate_rejects_mixed_session_and_claims_principal(monkeypatch) -> None:
    """F3-WS(AUDIT_2026-09-30) 재현: 세션 A의 sid + 사용자 B의 sub/tid로 서명된
    "혼합" 토큰은 REST(F3, task-9449)와 동일하게 WS 연결에서도 거부돼야 한다."""
    session_id = uuid4()
    user_a = uuid4()
    tenant_a = user_a
    user_b = uuid4()
    tenant_b = user_b

    session_a = _active_session(
        session_id=session_id, user_id=user_a, tenant_id=tenant_a, auth_level="PASSWORD"
    )
    mixed_claims = _claims(
        user_id=user_b, tenant_id=tenant_b, session_id=session_id, auth_level="PASSWORD"
    )

    monkeypatch.setattr(
        market_ws_dispatch,
        "get_token_verifier",
        lambda: SimpleNamespace(verify=lambda _t: mixed_claims),
    )
    monkeypatch.setattr(market_ws_dispatch, "get_user_by_id", _async_return(_active_user(user_b)))
    monkeypatch.setattr(session_repository, "get_active", _async_return(session_a))

    result = await market_ws_dispatch.authenticate(
        _AuthFakeWebSocket(token="irrelevant", pool=_FakePool())
    )

    assert result is None, "세션 소유자와 다른 sub/tid claims는 연결을 거부해야 한다"


async def test_authenticate_accepts_matching_session_and_claims(monkeypatch) -> None:
    """회귀 없음: 세션 자신의 소유자(sub/tid/auth_level 모두 일치)로 서명된
    정상 토큰은 기존대로 WS 연결이 수립돼야 한다."""
    session_id = uuid4()
    user_id = uuid4()
    tenant_id = user_id

    session = _active_session(
        session_id=session_id, user_id=user_id, tenant_id=tenant_id, auth_level="PASSWORD"
    )
    legit_claims = _claims(
        user_id=user_id, tenant_id=tenant_id, session_id=session_id, auth_level="PASSWORD"
    )

    monkeypatch.setattr(
        market_ws_dispatch,
        "get_token_verifier",
        lambda: SimpleNamespace(verify=lambda _t: legit_claims),
    )
    monkeypatch.setattr(market_ws_dispatch, "get_user_by_id", _async_return(_active_user(user_id)))
    monkeypatch.setattr(session_repository, "get_active", _async_return(session))

    result = await market_ws_dispatch.authenticate(
        _AuthFakeWebSocket(token="irrelevant", pool=_FakePool())
    )

    assert result == (tenant_id, user_id)


def _async_return(value: Any):
    async def _fn(*_args: Any, **_kwargs: Any) -> Any:
        return value

    return _fn
