"""LA-24 market_data HTTP read API (L4_market_data_positions_ledger_v1.0#LA-24).
4 endpoints 핵심 경로: 심볼/instrument_id 조회, 페이지네이션, 교차 테넌트 404,
coverage-gap 409, instruments/aliases 목록, 입력 검증, 수치 성능.

엔타이틀먼트/소스계약 거부 경로(DC-28)는 test_market_data_router_entitlement.py,
순수 커서 로직은 test_market_data_router_pure.py — 공유 fixture/헬퍼는 conftest.py.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from src.main import app
from tests.integration.api.conftest import BASE, _seed_candles, _span


async def test_candles_by_symbol_returns_envelope_with_both_ids_and_entitlement(client, seeded):
    params = {
        "venue": "BITGET",
        "timeframe": "1m",
        "symbol": seeded["symbol"],
        **_span(seeded["t0"], 0, 3),
    }
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
    base = {
        "venue": "BITGET",
        "timeframe": "1m",
        "instrument_id": str(seeded["instrument_id"]),
        "limit": 2,
        **_span(seeded["t0"], 0, 3),
    }
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
        params={
            "venue": "BITGET",
            "timeframe": "1m",
            "symbol": seeded["symbol"],
            **_span(seeded["t0"], 10, 12),
        },
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
        f"{BASE}/instruments/{seeded['symbol']}/aliases",
        params={"venue": "BITGET"},
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
        params={
            "venue": "BITGET",
            "timeframe": "1m",
            "symbol": seeded["symbol"],
            **span,
            "as_of": (datetime.now(timezone.utc) + timedelta(days=1)).isoformat(),
        },
        headers=seeded["a"],
    )
    assert future.status_code == 400, future.text


async def test_unauthenticated_request_is_401_envelope(client):
    response = await client.get(f"{BASE}/instruments")
    assert response.status_code == 401
    assert "error_code" in response.json()


# --- DEEPEN task-2994 (docs/audit/DEPTH_LA_LB_LC.md, 원 task-1376 D1) — 이
# 리프의 D3 하한 미달 중 수치 성능 단언을 채운다. ---


@pytest.mark.perf
async def test_candles_full_page_latency_stays_within_normalized_ceiling(
    client, seeded, perf_budget
):
    """수치 성능 단언 — baseline 대비 정규화 상한 게이트
    (time.perf_counter() → perf_budget.sample_async() 전환, task-11033).

    비동기 I/O는 sample_async로 wall_ms를 얻고 baseline * 20 + 0.5 상한과
    비교한다. 예산 값(20배 + 0.5s)은 그대로 유지한다.
    """
    t1 = seeded["t0"] + timedelta(minutes=100)
    pool = app.state.pool
    async with pool.acquire() as conn, conn.transaction():
        await _seed_candles(conn, pool, seeded["instrument_id"], t1, 200)
    span = _span(t1, 0, 200)
    base_params = {"venue": "BITGET", "timeframe": "1m", "symbol": seeded["symbol"], **span}

    baseline_sample = await perf_budget.sample_async(
        lambda: client.get(
            f"{BASE}/candles", params={**base_params, "limit": 1}, headers=seeded["a"]
        )
    )
    baseline = baseline_sample.result
    assert baseline.status_code == 200, baseline.text
    baseline_elapsed = baseline_sample.wall_ms / 1000

    full_sample = await perf_budget.sample_async(
        lambda: client.get(
            f"{BASE}/candles", params={**base_params, "limit": 200}, headers=seeded["a"]
        )
    )
    full = full_sample.result
    assert full.status_code == 200, full.text
    assert len(full.json()["data"]["candles"]) == 200
    full_elapsed = full_sample.wall_ms / 1000

    ceiling = baseline_elapsed * 20 + 0.5
    assert full_elapsed <= ceiling, (
        f"200개 캔들 전체 페이지 조회가 {full_elapsed:.3f}s 걸림 "
        f"(baseline {baseline_elapsed:.3f}s, 정규화 상한 {ceiling:.3f}s)"
    )
