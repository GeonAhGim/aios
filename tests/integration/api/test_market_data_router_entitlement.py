"""DC-28 market_data 엔타이틀먼트/소스계약 거부 경로 — source_contract PK 격리
(_FakeSourceContractRepository override), 엔타이틀먼트 포트 장애 fail-closed,
게이트 적색 재현(어댑터가 tenant_id를 무시하는 결함 대조).

LA-24 핵심 read 경로는 test_market_data_router.py, 공유 fixture/헬퍼는
conftest.py — 둘 다 이 파일과 함께 L4_market_data_positions_ledger_v1.0#LA-24
DoD를 채운다.
"""

from __future__ import annotations

from datetime import datetime, timezone

from src.api.foundation_deps import get_entitlement_port, get_venue_registry_source
from src.api.routers.market_data import get_source_contract_repository
from src.foundation.market_data.contracts.v1 import Venue
from src.foundation.market_data.domain.entitlement.source_contract import RedistributionScope
from src.main import app
from tests.integration.api.conftest import (
    BASE,
    _FakeSourceContractRepository,
    _source_contract,
    _span,
)


async def test_internal_scope_source_denies_candles_display(client, seeded):
    """DC-28 DoD — INTERNAL 계약 소스는 차트(캔들) 응답에 포함되면 안
    된다. 미등록 심볼과 동형인 404로 접힌다(재배포 스코프 거부도 존재
    누설 문제이므로 authorize_feed 거부와 같은 취급, read_api.py 원칙)."""
    app.dependency_overrides[get_source_contract_repository] = lambda: (
        _FakeSourceContractRepository(_source_contract(RedistributionScope.INTERNAL))
    )
    response = await client.get(
        f"{BASE}/candles",
        params={
            "venue": "BITGET",
            "timeframe": "1m",
            "symbol": seeded["symbol"],
            **_span(seeded["t0"], 0, 3),
        },
        headers=seeded["a"],
    )
    assert response.status_code == 404, response.text
    assert response.json()["error_code"] == "RESOURCE_NOT_FOUND"


async def test_internal_scope_source_denies_replay(client, seeded):
    app.dependency_overrides[get_source_contract_repository] = lambda: (
        _FakeSourceContractRepository(_source_contract(RedistributionScope.INTERNAL))
    )
    as_of = datetime.now(timezone.utc).isoformat()
    response = await client.get(
        f"{BASE}/candles/replay",
        params={
            "venue": "BITGET",
            "timeframe": "1m",
            "symbol": seeded["symbol"],
            "as_of": as_of,
            **_span(seeded["t0"], 0, 3),
        },
        headers=seeded["a"],
    )
    assert response.status_code == 404, response.text
    assert response.json()["error_code"] == "RESOURCE_NOT_FOUND"


async def test_unspecified_source_contract_denies_candles(client, seeded):
    """D2 "미지정은 NONE으로 취급" — 계약 행 자체가 없으면(`NOT_FOUND`) 조용히
    통과하지 않고 거부한다."""
    app.dependency_overrides[get_source_contract_repository] = lambda: (
        _FakeSourceContractRepository(None)
    )
    response = await client.get(
        f"{BASE}/candles",
        params={
            "venue": "BITGET",
            "timeframe": "1m",
            "symbol": seeded["symbol"],
            **_span(seeded["t0"], 0, 3),
        },
        headers=seeded["a"],
    )
    assert response.status_code == 404, response.text
    assert response.json()["error_code"] == "RESOURCE_NOT_FOUND"


# --- DEEPEN task-2994 (docs/audit/DEPTH_LA_LB_LC.md, 원 task-1376 D1) — 이
# 리프의 D3 하한 미달 중 failure-injection·게이트 적색 재현을 채운다. ---


class _BoomEntitlementPort:
    """실패 주입 — 엔타이틀먼트 포트가 예외를 던진다(DB 커넥션 유실 등
    실장애 시뮬레이션). `authorize_feed`가 이 예외를 삼켜 "허용"으로
    바꿔치기하면 fail-open이 된다."""

    async def allowed(self, subject, feed):  # noqa: ANN001, ARG002
        raise ConnectionError("simulated entitlement backend outage")


async def test_entitlement_port_failure_fails_closed_not_open(client, seeded):
    """실패 주입 — 엔타이틀먼트 포트 장애는 데이터 노출(fail-open)이 아니라
    500 `INTERNAL_ERROR` 봉투로 접혀야 한다(전역 핸들러,
    src/api/contracts/handlers.py). 원인 문자열도 클라이언트에 노출되지
    않는다(고정 메시지 + trace_id만)."""
    app.dependency_overrides[get_entitlement_port] = lambda: _BoomEntitlementPort()
    try:
        response = await client.get(
            f"{BASE}/candles",
            params={
                "venue": "BITGET",
                "timeframe": "1m",
                "symbol": seeded["symbol"],
                **_span(seeded["t0"], 0, 3),
            },
            headers=seeded["a"],
        )
    finally:
        app.dependency_overrides.pop(get_entitlement_port, None)

    assert response.status_code == 500, response.text
    body = response.json()
    assert body["error_code"] == "INTERNAL_ERROR"
    assert "data" not in body, "장애 상황에서 candles 데이터가 노출되면 안 된다(fail-closed)"
    assert "simulated entitlement backend outage" not in body["message"]


class _LeakyVenueRegistrySource:
    """게이트 적색 재현용 결함 시뮬레이션 — `registered_venues`가
    `tenant_id` 인자를 무시하고 모든 테넌트에게 BITGET을 등록된 것으로
    답한다(어댑터 SQL이 tenant_id WHERE 절을 빠뜨리는 흔한 실수)."""

    async def registered_venues(self, tenant_id):  # noqa: ANN001, ARG002
        return frozenset({Venue.BITGET})


async def test_aliases_gate_red_reproduction_if_venue_registry_source_ignores_tenant(
    client, seeded
):
    """게이트 적색 재현 — `authorize_venue()`(read_api.py)의 교차 테넌트
    방어는 `VenueRegistrySource`가 tenant_id로 올바르게 스코프될 때만
    유효하다. 이 어댑터가 tenant 필터링을 빠뜨리면, 정상 시나리오에서는
    404였던 타 테넌트 조회(test_market_data_router.py의
    test_aliases_by_symbol_and_uuid_and_cross_tenant_404)가 200으로
    새어나간다 — 이 결함을 대조 재현한다(200이 "정상"이라는 뜻이 아니라
    재현된 결함이라는 뜻)."""
    app.dependency_overrides[get_venue_registry_source] = lambda: _LeakyVenueRegistrySource()
    try:
        leaked = await client.get(
            f"{BASE}/instruments/{seeded['instrument_id']}/aliases", headers=seeded["b"]
        )
    finally:
        app.dependency_overrides.pop(get_venue_registry_source, None)

    assert leaked.status_code == 200, (
        "VenueRegistrySource가 tenant_id를 무시하면 authorize_venue()의 방어는 무력화된다"
        f" — got {leaked.status_code}: {leaked.text}"
    )
    assert leaked.json()["data"][0]["instrument_id"] == str(seeded["instrument_id"])
