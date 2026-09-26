"""UX-8(task-7773) — `POST /v1/foundation/screener/run` 통합테스트(실제 FastAPI 앱).

Spec: docs/specs/L4_product_experience_and_discovery_v1.0.md §2.2/§9 UX-6/UX-8.
DoD: 신규 라우터의 200 정상 1건 + negative >=3(잘못된 universe/cursor/미인증) +
실패주입 1건(스캔 도중 예외 -> 500) + 성능 수치 단언 1건(SCAN_TIMEOUT_SECONDS
예산 이내).

`ScreenerFieldSource`는 Protocol이라 `backtests.py` 계열처럼 실DB 캔들을
심는 대신, 인메모리 페이크로 `get_screener_field_source`를 덮어쓴다 — UX-6
엔진(`run_screen.py`) 자체는 이미 구현/테스트돼 있으므로 이 라우터 테스트는
"라우터가 엔진을 올바르게 호출하고 도메인 예외를 200/400/500으로 바르게
번역하는가"만 증명하면 된다.
"""

from __future__ import annotations

import time
import uuid
from collections.abc import AsyncIterator, Mapping, Sequence
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any
from uuid import UUID

import jwt
import pytest
from httpx import ASGITransport, AsyncClient

from src.api.routers.screener import get_screen_result_cache, get_screener_field_source
from src.data.models.base import AssetClass
from src.foundation.market_data.contracts.v1 import InstrumentRef, Venue
from src.foundation.market_data.contracts.v1_enums import SymbolStatus
from src.foundation.screener.application.run_screen import SCAN_TIMEOUT_SECONDS, ScreenResultCache
from src.main import app

STRONG_PASSWORD = "Str0ng!Passw0rd"
PATH = "/v1/foundation/screener/run"


def _instrument(symbol: str, *, venue: Venue = Venue.BITGET) -> InstrumentRef:
    return InstrumentRef(
        instrument_id=uuid.uuid4(),
        venue=venue,
        canonical_symbol=symbol,
        venue_symbol=symbol,
        asset_class=AssetClass.CRYPTO,
        base=symbol,
        quote="USDT",
        tick_size=Decimal("0.01"),
        lot_size=Decimal("0.0001"),
        status=SymbolStatus.LISTED,
        listed_at=datetime.now(timezone.utc),
        delisted_at=None,
    )


class _FakeFieldSource:
    """In-memory `ScreenerFieldSource` — see the module docstring for why this
    replaces the usual real-DB seeding pattern here."""

    def __init__(
        self, instruments: Sequence[InstrumentRef], fields: dict[UUID, dict[str, Decimal]]
    ) -> None:
        self._instruments = list(instruments)
        self._fields = fields
        self.read_fields_calls = 0

    async def universe_page(
        self, *, venues: frozenset[Venue], after: UUID | None, limit: int
    ) -> list[InstrumentRef]:
        candidates = sorted(
            (i for i in self._instruments if i.venue in venues), key=lambda i: i.instrument_id
        )
        if after is not None:
            candidates = [i for i in candidates if i.instrument_id > after]
        return candidates[:limit]

    async def read_fields(
        self,
        *,
        instrument_ids_by_venue: Mapping[Venue, Sequence[UUID]],
        field_names: frozenset[str],
        as_of: datetime,
    ) -> dict[UUID, dict[str, Decimal]]:
        self.read_fields_calls += 1
        result: dict[UUID, dict[str, Decimal]] = {}
        for ids in instrument_ids_by_venue.values():
            for instrument_id in ids:
                row = self._fields.get(instrument_id)
                if row is not None:
                    result[instrument_id] = row
        return result


class _ExplodingFieldSource:
    """실패주입 — 스캔 도중(첫 `read_fields` 호출) 예외를 던진다. 도메인 예외
    타입이 아니므로 exception_registry에 매핑이 없고, 전역 핸들러의 catch-all
    경로(500 `INTERNAL_ERROR`)로 떨어진다."""

    async def universe_page(
        self, *, venues: frozenset[Venue], after: UUID | None, limit: int
    ) -> list[InstrumentRef]:
        return [_instrument("BOOM")]

    async def read_fields(self, **kwargs: Any) -> dict[UUID, dict[str, Decimal]]:
        raise RuntimeError("field store unavailable")


@pytest.fixture
async def client() -> AsyncIterator[AsyncClient]:
    async with app.router.lifespan_context(app):
        transport = ASGITransport(app=app, raise_app_exceptions=False)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            yield ac
        app.dependency_overrides.pop(get_screener_field_source, None)
        app.dependency_overrides.pop(get_screen_result_cache, None)


async def _register(client: AsyncClient) -> dict:
    response = await client.post(
        "/auth/register",
        json={"email": f"test-{uuid.uuid4().hex}@example.com", "password": STRONG_PASSWORD},
    )
    token = response.json()["data"]["access_token"]
    jwt.decode(token, options={"verify_signature": False})
    return {"Authorization": f"Bearer {token}"}


def _override(client: AsyncClient, field_source: object) -> None:
    app.dependency_overrides[get_screener_field_source] = lambda: field_source
    app.dependency_overrides[get_screen_result_cache] = lambda: ScreenResultCache()


def _body(*, universe: str = "BITGET", condition: str = "close > 0") -> dict:
    return {
        "universe": universe,
        "filters": [{"kind": "indicator", "condition": condition}],
    }


# ---- I-10 배선 증명 ----


def test_route_is_mounted_on_app() -> None:
    paths = app.openapi()["paths"]
    assert PATH in paths
    assert "post" in paths[PATH]


def test_router_has_zero_raw_http_exception() -> None:
    import ast
    from pathlib import Path

    source = (Path(__file__).resolve().parents[3] / "src/api/routers/screener.py").read_text(
        "utf-8"
    )
    calls = [
        n
        for n in ast.walk(ast.parse(source))
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == "HTTPException"
    ]
    assert calls == []


# ---- 정상 200 ----


async def test_screen_run_success_returns_matched_rows(client: AsyncClient) -> None:
    headers = await _register(client)
    matching = _instrument("MATCH")
    non_matching = _instrument("NOMATCH")
    fields = {
        matching.instrument_id: {"close": Decimal("10")},
        non_matching.instrument_id: {"close": Decimal("-1")},
    }
    _override(client, _FakeFieldSource([matching, non_matching], fields))

    response = await client.post(PATH, json=_body(), headers=headers)

    assert response.status_code == 200, response.text
    body = response.json()
    assert set(body) == {"data", "meta"}
    data = body["data"]
    assert data["total"] == 1
    assert data["truncated"] is False
    assert [row["symbol"] for row in data["rows"]] == ["MATCH"]
    assert data["rows"][0]["values"] == {"close": "10"}


# ---- negative >=3 ----


async def test_unknown_venue_in_universe_is_400_not_500(client: AsyncClient) -> None:
    headers = await _register(client)
    _override(client, _FakeFieldSource([], {}))

    response = await client.post(PATH, json=_body(universe="NOT_A_REAL_VENUE"), headers=headers)

    assert response.status_code == 400, response.text
    assert response.json()["error_code"] == "VALIDATION_INVALID_FIELD"


async def test_invalid_cursor_is_400(client: AsyncClient) -> None:
    headers = await _register(client)
    _override(client, _FakeFieldSource([], {}))

    response = await client.post(f"{PATH}?cursor=not-a-number", json=_body(), headers=headers)

    assert response.status_code == 400, response.text
    assert response.json()["error_code"] == "VALIDATION_INVALID_FIELD"


async def test_unrecognized_field_name_is_400(client: AsyncClient) -> None:
    headers = await _register(client)
    _override(client, _FakeFieldSource([], {}))

    response = await client.post(PATH, json=_body(condition="market_cap > 0"), headers=headers)

    assert response.status_code == 400, response.text
    assert response.json()["error_code"] == "VALIDATION_INVALID_FIELD"


async def test_empty_filters_is_400_not_422(client: AsyncClient) -> None:
    """`ScreenDefinition._check_filters`(contracts/v1.py)의 `ValueError`는
    FastAPI 바디 파싱 중 `RequestValidationError`가 되고, 이 앱의
    `handlers.py`는 그걸 기본 422가 아니라 400 `VALIDATION_INVALID_FIELD`로
    번역한다(다른 라우터들의 negative 400 테스트와 동일 관례)."""
    headers = await _register(client)
    _override(client, _FakeFieldSource([], {}))

    response = await client.post(PATH, json={"universe": "BITGET", "filters": []}, headers=headers)

    assert response.status_code == 400, response.text
    assert response.json()["error_code"] == "VALIDATION_INVALID_FIELD"


async def test_unauthenticated_is_401(client: AsyncClient) -> None:
    _override(client, _FakeFieldSource([], {}))

    response = await client.post(PATH, json=_body())

    assert response.status_code == 401
    assert "error_code" in response.json()


# ---- 실패주입 1건 ----


async def test_field_source_failure_mid_scan_is_500_not_swallowed(client: AsyncClient) -> None:
    headers = await _register(client)
    _override(client, _ExplodingFieldSource())

    response = await client.post(PATH, json=_body(), headers=headers)

    assert response.status_code == 500, response.text
    assert response.json()["error_code"] == "INTERNAL_ERROR"


# ---- 성능 수치 단언 1건 ----


async def test_screen_run_completes_well_within_scan_timeout_budget(client: AsyncClient) -> None:
    headers = await _register(client)
    instruments = [_instrument(f"SYM{i}") for i in range(50)]
    fields = {i.instrument_id: {"close": Decimal("10")} for i in instruments}
    _override(client, _FakeFieldSource(instruments, fields))

    start = time.monotonic()
    response = await client.post(PATH, json=_body(), headers=headers)
    elapsed = time.monotonic() - start

    assert response.status_code == 200, response.text
    # 인메모리 페이크 소스이므로 §9 UX-6 DoD의 <=10s 예산(SCAN_TIMEOUT_SECONDS)에
    # 훨씬 못 미쳐야 한다 -- 라우터가 엔진에 불필요한 오버헤드를 추가하지 않는다는
    # 증거로 예산의 1/10을 문턱으로 쓴다.
    assert elapsed < SCAN_TIMEOUT_SECONDS / 10
