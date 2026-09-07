"""BT-10c — `POST /v1/backtests/quick` 통합테스트(실제 FastAPI 앱 + TEST_DATABASE_URL).

Spec: docs/specs/L4_analytics_authoring_backtest_marketplace_v1.0.md#§9.5 BT-10.
DoD: 실DB 캔들 시드 200 정상 1건 + negative 4종(봉 상한 초과 400, 컴파일 오류
400 details.line/col, 캔들 0봉 400, 타 테넌트/미인증 401·403) + 캔들 조회
왕복 1회 증명.

시드는 test_market_data_router.py(LA-24)와 같은 방식(md_instrument 직접
INSERT + LA-13 어댑터로 배치·캔들 저장)이다. "봉 상한 초과"는 실제로
`MAX_QUICK_BARS`(44,640)개 캔들을 심는 대신, 라우터가 호출 시점에 참조하는
`backtests.MAX_QUICK_BARS`를 테스트에서 낮춰(monkeypatch) 같은 코드 경로를
값싸게 재현한다 — `run_quick_backtest` 자체의 상한 검사(`_validate`)는
바뀌지 않는다.

DC-28(ADR-2026-09-06-H D2) — `source_contract`의 `source_id` PK를 실DB에
심으면 test_market_data_router.py와 공유돼 오염된다(그 파일 모듈 docstring
참조). 같은 이유로 `get_source_contract_repository`를 페이크로 덮어쓴다.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import asyncpg
import jwt
import pytest
from httpx import ASGITransport, AsyncClient

from src.api.foundation_deps import get_candle_store
from src.api.routers import backtests as backtests_router
from src.api.routers.backtests import get_source_contract_repository
from src.foundation.market_data.adapters.postgres_batch_repository import PostgresBatchRepository
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
from src.foundation.market_data.domain.entitlement.source_contract import (
    RedistributionScope,
    SourceCapability,
    SourceContract,
    SourceContractTier,
)
from src.main import app


def _source_contract(scope: RedistributionScope, *, source_id: str = "BITGET") -> SourceContract:
    now = datetime.now(timezone.utc)
    return SourceContract(
        source_id=source_id, tier=SourceContractTier.ENTERPRISE, credential_ref="test:none",
        redistribution_scope=scope, rate_limit=1000, quota=1_000_000,
        valid_from=now - timedelta(days=365), valid_to=None,
        capability=SourceCapability(
            asset_classes=frozenset({"CRYPTO"}), resolutions=frozenset({"1m"}),
        ),
    )


class _FakeSourceContractRepository:
    def __init__(self, contract: SourceContract | None) -> None:
        self._contract = contract

    async def get(self, conn: asyncpg.Connection, source_id: str) -> SourceContract | None:
        return self._contract

STRONG_PASSWORD = "Str0ng!Passw0rd"
PATH = "/v1/backtests/quick"

_SOURCE = "input close: series<float> = 0\norder(buy, 1) when close > 0\n"


def _config() -> dict:
    return {
        "slippage": {"kind": "fixed", "bps": "0"},
        "commission": {"venue": "BITGET", "maker_bps": "0", "taker_bps": "0", "min_fee": "0"},
        "latency_ms": 0,
        "partial_fill": {"max_participation_pct": "1"},
        "order_types": {"limit": True, "stop": True, "oco": False, "trailing": False},
        "magnifier_tf": None,
        "costs": {"funding": False, "borrow_apr": None},
        "adjustments": {"splits": False, "dividends": False},
        "calendar": "24x7",
    }


class _CountingCandleStore:
    """`read_candles_columnar` 호출 횟수만 세는 위임 래퍼(왕복 1회 증명용)."""

    def __init__(self, inner: PostgresCandleStore) -> None:
        self._inner = inner
        self.read_calls = 0

    async def upsert_batch(self, conn, batch_id, candles):
        return await self._inner.upsert_batch(conn, batch_id, candles)

    async def quarantine(self, conn, batch_id, candles, issues):
        return await self._inner.quarantine(conn, batch_id, candles, issues)

    async def query(self, conn, key, start, end, as_of):
        return await self._inner.query(conn, key, start, end, as_of)

    async def last_open_time(self, conn, key):
        return await self._inner.last_open_time(conn, key)

    async def read_candles_columnar(self, conn, key, start, end, as_of):
        self.read_calls += 1
        return await self._inner.read_candles_columnar(conn, key, start, end, as_of)


@pytest.fixture
async def client():
    async with app.router.lifespan_context(app):
        counting = _CountingCandleStore(PostgresCandleStore(app.state.pool))
        app.dependency_overrides[get_candle_store] = lambda: counting
        app.dependency_overrides[get_source_contract_repository] = lambda: (
            _FakeSourceContractRepository(_source_contract(RedistributionScope.DISPLAY))
        )
        transport = ASGITransport(app=app, raise_app_exceptions=False)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            ac.candle_store = counting  # type: ignore[attr-defined]
            yield ac
        app.dependency_overrides.pop(get_candle_store, None)
        app.dependency_overrides.pop(get_source_contract_repository, None)


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
    return instrument_id, symbol


async def _audit_event_id(conn: asyncpg.Connection) -> uuid.UUID:
    return await conn.fetchval(
        "INSERT INTO foundation_audit_event "
        "(sequence_no, aggregate_type, aggregate_id, action, outcome, trace_id, "
        " payload_hash, payload, event_hash) "
        "VALUES ($1, 'test.backtests', gen_random_uuid(), 'test.bt.ingest', 'SUCCESS', "
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


@pytest.fixture
async def seeded(client: AsyncClient) -> dict:
    headers_a, tenant_a = await _register(client)
    t0 = datetime.now(timezone.utc).replace(second=0, microsecond=0) - timedelta(minutes=30)
    pool = app.state.pool
    async with pool.acquire() as conn, conn.transaction():
        instrument_id, symbol = await _seed_instrument(conn, t0 - timedelta(days=1))
        await _seed_candles(conn, pool, instrument_id, t0, 10)
    return {
        "a": headers_a, "tenant_a": tenant_a, "instrument_id": instrument_id, "symbol": symbol,
        "t0": t0,
    }


def _body(seeded: dict, *, minutes: int = 10, source: str = _SOURCE) -> dict:
    t0 = seeded["t0"]
    return {
        "venue": "BITGET",
        "instrument_id": str(seeded["instrument_id"]),
        "timeframe": "1m",
        "start": t0.isoformat(),
        "end": (t0 + timedelta(minutes=minutes)).isoformat(),
        "initial_cash": "10000",
        "config": _config(),
        "script_source": source,
    }


# ---- I-10 배선 증명 ----


def test_route_is_mounted_on_app() -> None:
    paths = app.openapi()["paths"]
    assert PATH in paths
    assert "post" in paths[PATH]


def test_router_has_zero_raw_http_exception() -> None:
    import ast
    from pathlib import Path

    source = (
        Path(__file__).resolve().parents[3] / "src/api/routers/backtests.py"
    ).read_text("utf-8")
    calls = [
        n
        for n in ast.walk(ast.parse(source))
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == "HTTPException"
    ]
    assert calls == []


# ---- 정상 200 ----


async def test_quick_backtest_success_envelope_and_single_candle_round_trip(
    client: AsyncClient, seeded: dict
) -> None:
    response = await client.post(PATH, json=_body(seeded), headers=seeded["a"])
    assert response.status_code == 200, response.text
    body = response.json()
    assert set(body) == {"data", "meta"}
    data = body["data"]
    assert data["bars"] == 10
    assert isinstance(data["cash"], str) and isinstance(data["final_equity"], str)
    for fill in data["fills"]:
        assert isinstance(fill["quantity"], str) and isinstance(fill["price"], str)
    # 결정론 순서: bar_index가 증가한다.
    assert [f["bar_index"] for f in data["fills"]] == sorted(f["bar_index"] for f in data["fills"])
    assert client.candle_store.read_calls == 1  # type: ignore[attr-defined]


# ---- negative 4종 ----


async def test_too_many_bars_is_400_with_bars_and_max_details(
    client: AsyncClient, seeded: dict, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(backtests_router, "MAX_QUICK_BARS", 5)
    response = await client.post(PATH, json=_body(seeded, minutes=10), headers=seeded["a"])
    assert response.status_code == 400, response.text
    body = response.json()
    assert body["error_code"] == "VALIDATION_INVALID_FIELD"
    assert body["details"] == {"bars": 10, "max": 5}


async def test_script_compile_error_is_400_with_line_and_col(
    client: AsyncClient, seeded: dict
) -> None:
    response = await client.post(
        PATH, json=_body(seeded, source="let a = 1 +"), headers=seeded["a"]
    )
    assert response.status_code == 400, response.text
    body = response.json()
    assert body["error_code"] == "VALIDATION_INVALID_FIELD"
    assert body["details"] == {"code": "SCRIPT_SYNTAX", "line": 1, "col": 12}


async def test_zero_candles_is_400(client: AsyncClient, seeded: dict) -> None:
    empty_start = seeded["t0"] + timedelta(days=5)
    body = {
        **_body(seeded),
        "start": empty_start.isoformat(),
        "end": (empty_start + timedelta(minutes=10)).isoformat(),
    }
    response = await client.post(PATH, json=body, headers=seeded["a"])
    assert response.status_code == 400, response.text
    assert response.json()["error_code"] == "VALIDATION_INVALID_FIELD"


async def test_unauthenticated_is_401(client: AsyncClient, seeded: dict) -> None:
    response = await client.post(PATH, json=_body(seeded))
    assert response.status_code == 401
    assert "error_code" in response.json()


async def test_cross_tenant_header_is_403(client: AsyncClient, seeded: dict) -> None:
    headers = {**seeded["a"], "X-Tenant-Id": str(uuid.uuid4())}
    response = await client.post(PATH, json=_body(seeded), headers=headers)
    assert response.status_code == 403, response.text
    assert response.json()["error_code"] == "AUTH_TENANT_MISMATCH"


# ---- DC-28 재배포 스코프 강제(차트·백테스트 경로) ----


async def test_internal_scope_source_still_permits_backtest(
    client: AsyncClient, seeded: dict
) -> None:
    """`INTERNAL_CALC`은 백테스트가 요구하는 가장 낮은 문턱이다 — INTERNAL
    스코프 소스도 내부 계산(백테스트)에는 여전히 쓸 수 있다(D2, `permits_use`
    매트릭스). 화면 표시(SHARED_DISPLAY)만 막힌다는 것과 대칭인 positive 케이스."""
    app.dependency_overrides[get_source_contract_repository] = lambda: (
        _FakeSourceContractRepository(_source_contract(RedistributionScope.INTERNAL))
    )
    response = await client.post(PATH, json=_body(seeded), headers=seeded["a"])
    assert response.status_code == 200, response.text


async def test_none_scope_source_denies_backtest(client: AsyncClient, seeded: dict) -> None:
    """DC-28 DoD — 재배포 스코프가 없는(NONE) 소스는 백테스트(내부 계산)
    경로에서도 캔들을 못 읽는다. `read_candles_columnar`가 호출되기 전에
    거부되므로 왕복 1회 증명(`read_calls`)도 0에서 멈춘다."""
    app.dependency_overrides[get_source_contract_repository] = lambda: (
        _FakeSourceContractRepository(_source_contract(RedistributionScope.NONE))
    )
    response = await client.post(PATH, json=_body(seeded), headers=seeded["a"])
    assert response.status_code == 404, response.text
    assert response.json()["error_code"] == "RESOURCE_NOT_FOUND"
    assert client.candle_store.read_calls == 0  # type: ignore[attr-defined]
