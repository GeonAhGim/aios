"""LA-24 — market_data HTTP 읽기 API 통합테스트(실제 FastAPI 앱 + TEST_DATABASE_URL).

Spec: docs/specs/L4_market_data_positions_ledger_v1.0.md#§9.2 LA-24.
DoD: 4 엔드포인트 통합 테스트 + 교차 테넌트 404(동형) + 커버리지 밖 span →
`DATA_COVERAGE_MISSING` 409.

시드는 test_get_candles.py(LA-17)와 같은 방식(md_instrument 직접 INSERT +
LA-13 어댑터로 배치·캔들 저장)에 `md_symbol_alias`(심볼 해석 경로)와 DC-8
`entitlements`(테넌트 A의 BITGET 등록)를 더한다. 테넌트 B는 아무 등록도
없다 — 같은 인스트루먼트가 B에게는 "없는 것"이어야 한다.
BITGET(연속 세션)만 쓴다 — 캘린더 시드가 필요 없다(LA-17 테스트와 동일 근거).
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import asyncpg
import jwt
import pytest
from httpx import ASGITransport, AsyncClient

from src.foundation.market_data.adapters.postgres_batch_repository import PostgresBatchRepository
from src.foundation.market_data.adapters.postgres_candle_store import PostgresCandleStore
from src.foundation.market_data.application.read_api import paginate_candles
from src.foundation.market_data.contracts.v1 import (
    CandleRecord,
    IngestBatchResult,
    QualityVerdict,
    SeriesKey,
    Timeframe,
    Venue,
    Verdict,
)
from src.main import app

STRONG_PASSWORD = "Str0ng!Passw0rd"
BASE = "/v1/foundation/market-data"


@pytest.fixture
async def client():
    async with app.router.lifespan_context(app):
        transport = ASGITransport(app=app, raise_app_exceptions=False)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            yield ac


async def _register(client: AsyncClient) -> tuple[dict, uuid.UUID]:
    response = await client.post(
        "/auth/register",
        json={"email": f"test-{uuid.uuid4().hex}@example.com", "password": STRONG_PASSWORD},
    )
    token = response.json()["data"]["access_token"]
    user_id = jwt.decode(token, options={"verify_signature": False})["sub"]
    return {"Authorization": f"Bearer {token}"}, uuid.UUID(user_id)


async def _seed_instrument(conn: asyncpg.Connection, listed_at: datetime) -> tuple[uuid.UUID, str]:
    symbol = f"TST{uuid.uuid4().hex[:10].upper()}"
    instrument_id = await conn.fetchval(
        "INSERT INTO md_instrument (venue, canonical_symbol, venue_symbol, asset_class, "
        " tick_size, lot_size, status, listed_at) "
        "VALUES ('BITGET', $1, $1, 'CRYPTO', 0.01, 0.0001, 'LISTED', $2) RETURNING instrument_id",
        symbol,
        listed_at,
    )
    await conn.execute(
        "INSERT INTO md_symbol_alias (instrument_id, venue, alias_symbol, valid_from) "
        "VALUES ($1, 'BITGET', $2, $3)",
        instrument_id,
        symbol,
        listed_at,
    )
    return instrument_id, symbol


async def _audit_event_id(conn: asyncpg.Connection) -> uuid.UUID:
    return await conn.fetchval(
        "INSERT INTO foundation_audit_event "
        "(sequence_no, aggregate_type, aggregate_id, action, outcome, trace_id, "
        " payload_hash, payload, event_hash) "
        "VALUES ($1, 'test.market_data', gen_random_uuid(), 'test.md.ingest', 'SUCCESS', "
        " gen_random_uuid(), 'deadbeef', '{}'::jsonb, 'deadbeef') RETURNING id",
        uuid.uuid4().int % (2**62),
    )


def _candle(key: SeriesKey, open_time: datetime, price: int) -> CandleRecord:
    return CandleRecord(
        key=key, open_time=open_time, close_time=open_time + timedelta(minutes=1),
        open=Decimal(price), high=Decimal(price + 10), low=Decimal(price - 10),
        close=Decimal(price + 5), volume=Decimal(10),
    )


async def _seed_candles(
    conn: asyncpg.Connection, pool: asyncpg.Pool, instrument_id: uuid.UUID, t0: datetime, n: int
) -> SeriesKey:
    key = SeriesKey(venue=Venue.BITGET, instrument_id=instrument_id, timeframe=Timeframe.M1)
    batch = IngestBatchResult(
        batch_id=uuid.uuid4(), source="test", venue=Venue.BITGET, instrument_id=instrument_id,
        timeframe=Timeframe.M1, range_start=t0, range_end=t0 + timedelta(minutes=n),
        request_fingerprint=f"fp-{uuid.uuid4().hex}",
        verdict=QualityVerdict(verdict=Verdict.ACCEPT, accepted=n, quarantined=0, rejected=0,
                               issues=[]),
        batch_hash=f"hash-{uuid.uuid4().hex}", audit_event_id=await _audit_event_id(conn),
        stored_range=None,
    )
    await PostgresBatchRepository(pool).create(conn, batch)
    candles = [_candle(key, t0 + timedelta(minutes=i), 100 + i) for i in range(n)]
    await PostgresCandleStore(pool).upsert_batch(conn, batch.batch_id, candles)
    return key


async def _grant_venue(conn: asyncpg.Connection, tenant_id: uuid.UUID) -> None:
    await conn.execute(
        "INSERT INTO entitlements (tenant_id, subject_id, venue, timeframe, feed_type) "
        "VALUES ($1, $1, 'BITGET', '1m', 'DELAYED')",
        tenant_id,
    )


@pytest.fixture
async def seeded(client: AsyncClient) -> dict:
    headers_a, tenant_a = await _register(client)
    headers_b, _tenant_b = await _register(client)
    t0 = datetime.now(timezone.utc).replace(second=0, microsecond=0) - timedelta(minutes=30)
    pool = app.state.pool
    async with pool.acquire() as conn, conn.transaction():
        instrument_id, symbol = await _seed_instrument(conn, t0 - timedelta(days=1))
        other_id, _ = await _seed_instrument(conn, t0 - timedelta(days=1))
        await _seed_candles(conn, pool, instrument_id, t0, 3)
        await _grant_venue(conn, tenant_a)
    return {
        "a": headers_a, "b": headers_b, "instrument_id": instrument_id, "other_id": other_id,
        "symbol": symbol, "t0": t0,
    }


def _span(t0: datetime, start_min: int, end_min: int) -> dict:
    return {
        "start": (t0 + timedelta(minutes=start_min)).isoformat(),
        "end": (t0 + timedelta(minutes=end_min)).isoformat(),
    }


async def test_candles_by_symbol_returns_envelope_with_both_ids_and_entitlement(client, seeded):
    params = {"venue": "BITGET", "timeframe": "1m", "symbol": seeded["symbol"],
              **_span(seeded["t0"], 0, 3)}
    response = await client.get(f"{BASE}/candles", params=params, headers=seeded["a"])

    assert response.status_code == 200, response.text
    body = response.json()
    assert set(body) >= {"data", "meta"} and body["meta"]["trace_id"]
    data = body["data"]
    assert data["instrument_id"] == str(seeded["instrument_id"])
    assert data["symbol"] == seeded["symbol"]
    assert data["key"]["instrument_id"] == str(seeded["instrument_id"])
    assert len(data["candles"]) == 3 and data["gaps"] == []
    assert data["entitlement"] == {"mode": "delayed", "delayed_seconds": 0}
    assert data["schema_version"] == "v1" and len(data["series_hash"]) == 64
    assert body["meta"]["page"]["next_cursor"] is None


async def test_candles_by_instrument_id_paginates_with_open_time_cursor(client, seeded):
    base = {"venue": "BITGET", "timeframe": "1m", "instrument_id": str(seeded["instrument_id"]),
            "limit": 2, **_span(seeded["t0"], 0, 3)}
    first = (await client.get(f"{BASE}/candles", params=base, headers=seeded["a"])).json()
    assert len(first["data"]["candles"]) == 2
    cursor = first["meta"]["page"]["next_cursor"]
    assert cursor is not None

    second = (
        await client.get(f"{BASE}/candles", params={**base, "cursor": cursor}, headers=seeded["a"])
    ).json()
    assert [c["open_time"] for c in second["data"]["candles"]] == [cursor]
    assert cursor == (seeded["t0"] + timedelta(minutes=2)).isoformat().replace("+00:00", "Z")
    assert second["meta"]["page"]["next_cursor"] is None
    # series_hash는 페이지가 아니라 요청 구간 전체의 해시 — 페이지 간 동일.
    assert second["data"]["series_hash"] == first["data"]["series_hash"]


async def test_cross_tenant_candles_is_404_isomorphic_with_unknown_symbol(client, seeded):
    span = _span(seeded["t0"], 0, 3)
    foreign = await client.get(
        f"{BASE}/candles",
        params={"venue": "BITGET", "timeframe": "1m", "symbol": seeded["symbol"], **span},
        headers=seeded["b"],
    )
    unknown = await client.get(
        f"{BASE}/candles",
        params={"venue": "BITGET", "timeframe": "1m", "symbol": "NOPE-NOPE", **span},
        headers=seeded["a"],
    )
    assert foreign.status_code == unknown.status_code == 404
    foreign_body, unknown_body = foreign.json(), unknown.json()
    assert foreign_body["error_code"] == unknown_body["error_code"] == "RESOURCE_NOT_FOUND"
    assert foreign_body["message"] == unknown_body["message"]
    assert set(foreign_body) == set(unknown_body) and "data" not in foreign_body


async def test_span_outside_coverage_is_409_data_coverage_missing(client, seeded):
    response = await client.get(
        f"{BASE}/candles",
        params={"venue": "BITGET", "timeframe": "1m", "symbol": seeded["symbol"],
                **_span(seeded["t0"], 10, 12)},
        headers=seeded["a"],
    )
    assert response.status_code == 409, response.text
    assert response.json()["error_code"] == "DATA_COVERAGE_MISSING"


async def test_replay_complete_span_ok_and_gap_span_409(client, seeded):
    as_of = datetime.now(timezone.utc).isoformat()
    base = {"venue": "BITGET", "timeframe": "1m", "symbol": seeded["symbol"], "as_of": as_of}
    complete = await client.get(
        f"{BASE}/candles/replay", params={**base, **_span(seeded["t0"], 0, 3)}, headers=seeded["a"]
    )
    assert complete.status_code == 200, complete.text
    data = complete.json()["data"]
    assert data["expected_count"] == 3 and data["missing_count"] == 0
    assert data["instrument_id"] == str(seeded["instrument_id"])

    gappy = await client.get(
        f"{BASE}/candles/replay", params={**base, **_span(seeded["t0"], 0, 5)}, headers=seeded["a"]
    )
    assert gappy.status_code == 409, gappy.text
    assert gappy.json()["error_code"] == "DATA_COVERAGE_MISSING"


async def test_instruments_list_is_scoped_to_registered_venues_and_paginates(client, seeded):
    page = (
        await client.get(f"{BASE}/instruments", params={"limit": 1}, headers=seeded["a"])
    ).json()
    assert len(page["data"]["items"]) == 1
    assert page["data"]["next_cursor"] == page["meta"]["page"]["next_cursor"] is not None

    wanted = {str(seeded["instrument_id"]), str(seeded["other_id"])}
    seen: set[str] = set()
    cursor: str | None = None
    for _ in range(200):
        params = {"limit": 200, "venue": "BITGET"} | ({"cursor": cursor} if cursor else {})
        data = (await client.get(f"{BASE}/instruments", params=params, headers=seeded["a"])).json()[
            "data"
        ]
        seen |= {item["instrument_id"] for item in data["items"]}
        cursor = data["next_cursor"]
        if cursor is None:
            break
    assert wanted <= seen

    foreign = (await client.get(f"{BASE}/instruments", headers=seeded["b"])).json()
    assert foreign["data"] == {"items": [], "next_cursor": None}


async def test_aliases_by_symbol_and_uuid_and_cross_tenant_404(client, seeded):
    by_symbol = await client.get(
        f"{BASE}/instruments/{seeded['symbol']}/aliases", params={"venue": "BITGET"},
        headers=seeded["a"],
    )
    assert by_symbol.status_code == 200, by_symbol.text
    aliases = by_symbol.json()["data"]
    assert [a["alias_symbol"] for a in aliases] == [seeded["symbol"]]
    assert aliases[0]["instrument_id"] == str(seeded["instrument_id"])

    by_uuid = await client.get(
        f"{BASE}/instruments/{seeded['instrument_id']}/aliases", headers=seeded["a"]
    )
    assert by_uuid.status_code == 200 and by_uuid.json()["data"] == aliases

    foreign = await client.get(
        f"{BASE}/instruments/{seeded['instrument_id']}/aliases", headers=seeded["b"]
    )
    assert foreign.status_code == 404 and foreign.json()["error_code"] == "RESOURCE_NOT_FOUND"

    no_venue = await client.get(
        f"{BASE}/instruments/{seeded['symbol']}/aliases", headers=seeded["a"]
    )
    assert no_venue.status_code == 400


async def test_negative_missing_identifier_and_future_as_of_are_400(client, seeded):
    span = _span(seeded["t0"], 0, 3)
    missing = await client.get(
        f"{BASE}/candles",
        params={"venue": "BITGET", "timeframe": "1m", **span},
        headers=seeded["a"],
    )
    assert missing.status_code == 400 and missing.json()["error_code"] == "VALIDATION_INVALID_FIELD"

    future = await client.get(
        f"{BASE}/candles",
        params={"venue": "BITGET", "timeframe": "1m", "symbol": seeded["symbol"], **span,
                "as_of": (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()},
        headers=seeded["a"],
    )
    assert future.status_code == 400, future.text


async def test_unauthenticated_request_is_401_envelope(client):
    response = await client.get(f"{BASE}/instruments")
    assert response.status_code == 401
    assert "error_code" in response.json()


def test_paginate_candles_pure_cursor_semantics():
    t0 = datetime(2026, 9, 1, tzinfo=timezone.utc)
    key = SeriesKey(venue=Venue.BITGET, instrument_id=uuid.uuid4(), timeframe=Timeframe.M1)
    candles = [_candle(key, t0 + timedelta(minutes=i), 100) for i in range(5)]

    page, nxt = paginate_candles(candles, None, 2)
    assert [c.open_time for c in page] == [t0, t0 + timedelta(minutes=1)]
    assert nxt == "2026-09-01T00:02:00Z"

    last, nxt2 = paginate_candles(candles, t0 + timedelta(minutes=4), 2)
    assert len(last) == 1 and nxt2 is None
    assert paginate_candles(candles, t0 + timedelta(minutes=9), 2) == ([], None)
