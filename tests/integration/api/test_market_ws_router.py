"""M2-1 — `/ws/market` WS 게이트웨이 통합테스트(실제 FastAPI 앱 + TEST_DATABASE_URL).

Spec: docs/design/ADR-2026-09-09-B-mvp1-hardening-and-mvp2-scope.md M2-1.
DoD: 인증 없는 접속 거부, 재접속 시 마지막 봉 이후 갭 REST 보충 계약.
"부하 테스트(구독 500, p95 500ms 이하)"는 순수 asyncio 단위테스트
(tests/unit/api/ws/test_market_ws_dispatch.py)로 분리했다 — 실제 소켓을 500개
열면 bcrypt 회원가입 비용까지 겹쳐 느려지고, 이 파일이 검증하려는 것(실제
JWT 인증 경로·구독 상한·REST 갭 계약)과는 다른 축이다.

`RealtimeFanout.publish()`를 직접 호출해 소켓까지 전달되는지 보는 테스트는
여기 없다 — `TestClient`는 앱을 별도 스레드(anyio portal)의 이벤트 루프에서
돌리므로, pytest 자신의 루프에서 그 포트 안의 `asyncio.Queue`를 건드리면
"different event loop" 오류가 난다. 전달 자체는 단위테스트가 실제 소켓 없이
같은 `_pump` 코루틴으로 이미 검증한다.
"""

from __future__ import annotations

import os
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import asyncpg
import jwt
import pytest
from starlette.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from src.api.routers.market_data import get_source_contract_repository
from src.api.ws.market_ws import reset_gateway_state_for_test
from src.foundation.market_data.domain.entitlement.source_contract import (
    RedistributionScope,
    SourceCapability,
    SourceContract,
    SourceContractTier,
)
from src.main import app

STRONG_PASSWORD = "Str0ng!Passw0rd"


def _asyncpg_dsn() -> str:
    return os.environ["DATABASE_URL"].replace("postgresql+asyncpg://", "postgresql://")


class _FakeSourceContractRepository:
    """`tests/integration/api/test_market_data_router.py`와 같은 이유로 같은
    패턴을 재사용한다 — `source_contract`는 `source_id`가 PK인 전역 테이블이라
    실DB에 BITGET 행을 심으면 다른 테스트와 공유돼 오염된다."""

    async def get(self, conn: asyncpg.Connection, source_id: str) -> SourceContract | None:
        now = datetime.now(timezone.utc)
        return SourceContract(
            source_id=source_id,
            tier=SourceContractTier.ENTERPRISE,
            credential_ref="test:none",
            redistribution_scope=RedistributionScope.DISPLAY,
            rate_limit=1000,
            quota=1_000_000,
            valid_from=now - timedelta(days=365),
            valid_to=None,
            capability=SourceCapability(
                asset_classes=frozenset({"CRYPTO"}), resolutions=frozenset({"1m"})
            ),
        )


@pytest.fixture(autouse=True)
def _reset_gateway_state():
    reset_gateway_state_for_test()
    yield
    reset_gateway_state_for_test()


@pytest.fixture
def client():
    app.dependency_overrides[get_source_contract_repository] = _FakeSourceContractRepository
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.pop(get_source_contract_repository, None)


def _register(client: TestClient) -> tuple[str, uuid.UUID]:
    response = client.post(
        "/auth/register",
        json={"email": f"test-{uuid.uuid4().hex}@example.com", "password": STRONG_PASSWORD},
    )
    assert response.status_code in (200, 201), response.text
    token = response.json()["data"]["access_token"]
    user_id = jwt.decode(token, options={"verify_signature": False})["sub"]
    return token, uuid.UUID(user_id)


async def _grant_realtime_venue(tenant_id: uuid.UUID, *, venue: str = "BITGET") -> None:
    pool = await asyncpg.create_pool(_asyncpg_dsn())
    try:
        async with pool.acquire() as conn:
            await conn.execute(
                "INSERT INTO entitlements (tenant_id, subject_id, venue, timeframe, feed_type) "
                "VALUES ($1, $1, $2, '1m', 'REALTIME')",
                tenant_id,
                venue,
            )
    finally:
        await pool.close()


async def _seed_instrument_with_one_candle(symbol: str, *, venue: str = "BITGET") -> datetime:
    """resume 계약이 가리키는 REST 경로가 실제로 봉을 돌려주는지(500/빈 결과가
    아니라 진짜 재접속 갭 보충이 되는지) 검증하려고 캔들 1건까지 심는다 —
    `tests/integration/api/test_market_data_router.py`의 `_seed_instrument`/
    `_seed_candles`와 같은 스키마."""
    from src.foundation.market_data.adapters.postgres_batch_repository import (
        PostgresBatchRepository,
    )
    from src.foundation.market_data.adapters.postgres_candle_store import PostgresCandleStore
    from src.foundation.market_data.contracts.v1 import (
        CandleRecord,
        IngestBatchResult,
        QualityVerdict,
        SeriesKey,
        Timeframe,
        Venue,
        Verdict,
    )

    listed_at = datetime.now(timezone.utc) - timedelta(days=1)
    t0 = datetime.now(timezone.utc).replace(second=0, microsecond=0) - timedelta(minutes=3)
    pool = await asyncpg.create_pool(_asyncpg_dsn())
    try:
        async with pool.acquire() as conn, conn.transaction():
            await conn.execute(
                "INSERT INTO md_instrument (venue, canonical_symbol, venue_symbol, asset_class, "
                " tick_size, lot_size, status, listed_at) "
                "VALUES ($1, $2, $2, 'CRYPTO', 0.01, 0.0001, 'LISTED', $3)",
                venue,
                symbol,
                listed_at,
            )
            instrument_id = await conn.fetchval(
                "SELECT instrument_id FROM md_instrument WHERE venue=$1 AND canonical_symbol=$2",
                venue,
                symbol,
            )
            await conn.execute(
                "INSERT INTO md_symbol_alias (instrument_id, venue, alias_symbol, valid_from) "
                "VALUES ($1, $2, $3, $4)",
                instrument_id,
                venue,
                symbol,
                listed_at,
            )
            audit_event_id = await conn.fetchval(
                "INSERT INTO foundation_audit_event "
                "(sequence_no, aggregate_type, aggregate_id, action, outcome, trace_id, "
                " payload_hash, payload, event_hash) "
                "VALUES ($1, 'test.market_data', gen_random_uuid(), 'test.md.ingest', "
                " 'SUCCESS', gen_random_uuid(), 'deadbeef', '{}'::jsonb, 'deadbeef') "
                "RETURNING id",
                uuid.uuid4().int % (2**62),
            )
            key = SeriesKey(venue=Venue.BITGET, instrument_id=instrument_id, timeframe=Timeframe.M1)
            batch = IngestBatchResult(
                batch_id=uuid.uuid4(),
                source="test",
                venue=Venue.BITGET,
                instrument_id=instrument_id,
                timeframe=Timeframe.M1,
                range_start=t0,
                range_end=t0 + timedelta(minutes=1),
                request_fingerprint=f"fp-{uuid.uuid4().hex}",
                verdict=QualityVerdict(
                    verdict=Verdict.ACCEPT, accepted=1, quarantined=0, rejected=0, issues=[]
                ),
                batch_hash=f"hash-{uuid.uuid4().hex}",
                audit_event_id=audit_event_id,
                stored_range=None,
            )
            await PostgresBatchRepository(pool).create(conn, batch)
            candle = CandleRecord(
                key=key,
                open_time=t0,
                close_time=t0 + timedelta(minutes=1),
                open=Decimal(100),
                high=Decimal(110),
                low=Decimal(90),
                close=Decimal(105),
                volume=Decimal(10),
            )
            await PostgresCandleStore(pool).upsert_batch(conn, batch.batch_id, [candle])
    finally:
        await pool.close()
    return t0


def _subscribe_message(instrument_id: str = "BTC-USDT") -> dict:
    return {
        "op": "subscribe",
        "venue": "BITGET",
        "asset_class": "CRYPTO",
        "instrument_id": instrument_id,
        "timeframe": "1m",
    }


def test_connect_without_token_is_rejected(client: TestClient) -> None:
    with pytest.raises(WebSocketDisconnect), client.websocket_connect("/ws/market"):
        pass


def test_connect_with_garbage_token_is_rejected(client: TestClient) -> None:
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect("/ws/market?token=not-a-real-jwt"):
            pass


def test_connect_with_expired_or_tampered_token_is_rejected(client: TestClient) -> None:
    token, _ = _register(client)
    tampered = token[:-4] + ("aaaa" if token[-4:] != "aaaa" else "bbbb")
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect(f"/ws/market?token={tampered}"):
            pass


async def test_subscribe_ack_carries_gap_backfill_rest_contract(client: TestClient) -> None:
    """재접속 갭 보충 계약 — 구독 ack의 resume 힌트 그대로 REST를 호출하면
    WS가 붙기 전에 이미 있던 봉(마지막 수신 이후 "갭")을 실제로 받아온다."""
    token, tenant_id = _register(client)
    await _grant_realtime_venue(tenant_id)
    symbol = f"TST{uuid.uuid4().hex[:10].upper()}"
    t0 = await _seed_instrument_with_one_candle(symbol)

    with client.websocket_connect(f"/ws/market?token={token}") as ws:
        ws.send_json(_subscribe_message(symbol))
        ack = ws.receive_json()

    assert ack["op"] == "subscribed"
    assert uuid.UUID(ack["subscription_id"])
    resume = ack["resume"]
    assert resume["rest_endpoint"] == "/v1/foundation/market-data/candles"
    assert resume["cursor_param"] == "start"
    assert resume["query"] == {"venue": "BITGET", "timeframe": "1m", "symbol": symbol}

    # resume 힌트를 문자 그대로 REST 호출에 적용한다 — cursor_param(start)에
    # 시드된 봉 이전 시각을 넣어, "재접속 전에 놓친 구간"을 실제로 메꾼다.
    response = client.get(
        resume["rest_endpoint"],
        params={
            **resume["query"],
            resume["cursor_param"]: (t0 - timedelta(minutes=1)).isoformat(),
            "end": (t0 + timedelta(minutes=1)).isoformat(),
        },
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 200, response.text
    candles = response.json()["data"]["candles"]
    assert len(candles) == 1
    assert datetime.fromisoformat(candles[0]["open_time"].replace("Z", "+00:00")) == t0


async def test_subscription_cap_is_enforced_per_tenant_and_released_on_unsubscribe(
    client: TestClient,
) -> None:
    token, tenant_id = _register(client)
    await _grant_realtime_venue(tenant_id)

    with client.websocket_connect(f"/ws/market?token={token}") as ws:
        subscription_ids: list[str] = []
        for _ in range(50):
            ws.send_json(_subscribe_message())
            ack = ws.receive_json()
            assert ack["op"] == "subscribed", ack
            subscription_ids.append(ack["subscription_id"])

        ws.send_json(_subscribe_message())
        denied = ws.receive_json()
        assert denied["op"] == "error"
        assert denied["code"] == "SUBSCRIPTION_LIMIT_EXCEEDED"

        ws.send_json({"op": "unsubscribe", "subscription_id": subscription_ids[0]})
        released = ws.receive_json()
        assert released["op"] == "unsubscribed"

        ws.send_json(_subscribe_message())
        reacquired = ws.receive_json()
        assert reacquired["op"] == "subscribed", (
            "unsubscribe 이후에는 다시 상한을 확보할 수 있어야 한다"
        )


async def test_malformed_subscribe_message_returns_error_without_closing_connection(
    client: TestClient,
) -> None:
    token, tenant_id = _register(client)
    await _grant_realtime_venue(tenant_id)

    with client.websocket_connect(f"/ws/market?token={token}") as ws:
        ws.send_json({"op": "subscribe", "venue": "NOT_A_VENUE"})
        error = ws.receive_json()
        assert error["op"] == "error"

        # 잘못된 메시지 하나가 연결 자체를 끊지 않는다 — 같은 소켓으로 정상
        # 구독이 이어서 성공해야 한다.
        ws.send_json(_subscribe_message())
        ack = ws.receive_json()
        assert ack["op"] == "subscribed"
