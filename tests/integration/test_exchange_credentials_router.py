"""16번대 통합테스트 — /exchange-credentials 라우터. 실제 FastAPI 앱 + 실제
dev DB. 실제 Bitget/KIS Demo 키가 없어(.env 비어있음) FastAPI
dependency_overrides로 가짜 adapter_factory를 주입한다(이 세션에서
반복 적용한 DI 패턴을 라우터 계층에도 그대로 적용).

DEEPEN(task-6732, ADR-2026-09-09-C D2 floor for PLT axis): 기존 커버리지는
negative 테스트 4개(400/404 x2/401)는 있었지만 실패 주입, 성능 단언, 게이트
적색 재현이 없었다. 이 파일 하단에 세 가지를 추가한다 — 키 로테이션으로 인한
복호화 실패 주입(`ExchangeCredentialDecryptionError` -> 400), 봉투 없는 목록
응답의 p95 지연 예산, 그리고 파일 상단 docstring이 주장하는 "PLT-17 봉투
래핑 시 check_openapi_compat.py가 MAJOR로 FAIL한다"는 근거를 `find_violations`
직접 호출로 재현(주장만 있고 검증이 없던 상태였다)."""

import copy
import json
import math
import time
import uuid
from pathlib import Path

import pytest
from fastapi import Depends
from httpx import ASGITransport, AsyncClient

from scripts.check_openapi_compat import find_violations
from src.api.deps import get_pool
from src.api.service_deps import get_credential_resolver, get_exchange_credential_service
from src.core.security.key_ring import KeyRing
from src.exchanges.common.types import ExchangeCapability
from src.main import app
from src.services.credential_resolver import CredentialResolver
from src.services.exchange_credential_service import ExchangeCredentialService

STRONG_PASSWORD = "Str0ng!Passw0rd"
ENCRYPTION_KEY = "44" * 32
KEY_RING = KeyRing.from_legacy_hex(ENCRYPTION_KEY)
_ROTATED_KEY_RING = KeyRing.from_legacy_hex("55" * 32)
_V1_SNAPSHOT_PATH = Path(__file__).resolve().parents[2] / "contracts" / "openapi" / "v1.json"
_LIST_CREDENTIALS_P95_BUDGET_SECONDS = 0.2  # ADR-2026-09-09-C에 이 축 전용 항목이
# 없어 표에서 가장 가까운 조회 항목("5k봉 조회 p95 200ms")을 차용 — 이 엔드포인트는
# 사용자당 최대 수 개 row를 읽는 단순 SELECT라 그보다 훨씬 가벼운데도 여유 있게 차용.


class _FakeAdapter:
    def __init__(self, api_key, api_secret, extra, *, fail=False):
        self.api_key = api_key
        self._fail = fail

    async def get_balance(self):
        if self._fail:
            raise RuntimeError("invalid credentials")
        return []

    async def get_positions(self):
        return []

    def get_capabilities(self):
        return ExchangeCapability(
            exchange_name="bitget",
            supported_asset_classes=[],
            supports_spot=True,
            supports_futures=False,
            supports_leverage=False,
            supports_websocket=True,
            max_leverage=1,
            reference_feed_coverage="high",
            has_official_sandbox=True,
        )

    async def aclose(self):
        pass


def _fake_factory(exchange, api_key, api_secret, extra, *, demo_mode=True):
    return _FakeAdapter(api_key, api_secret, extra, fail=(api_key == "bad-key"))


async def _override_credential_service(pool=Depends(get_pool)):
    return ExchangeCredentialService(pool, key_ring=KEY_RING, adapter_factory=_fake_factory)


async def _override_resolver(pool=Depends(get_pool)):
    service = ExchangeCredentialService(pool, key_ring=KEY_RING, adapter_factory=_fake_factory)
    return CredentialResolver(service, adapter_factory=_fake_factory)


@pytest.fixture
async def client():
    async with app.router.lifespan_context(app):
        app.dependency_overrides[get_exchange_credential_service] = _override_credential_service
        app.dependency_overrides[get_credential_resolver] = _override_resolver
        # raise_app_exceptions=False — ExchangeCredentialError/CredentialNotFoundError는
        # 이제 전역 Exception 핸들러(src/api/contracts/handlers.py)를 거친다.
        # test_auth_router.py client 픽스처 주석과 동일 근거(Starlette가 그
        # 핸들러를 ServerErrorMiddleware로 승격시켜 정상 응답 뒤에도 예외를
        # 재전파한다).
        transport = ASGITransport(app=app, raise_app_exceptions=False)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            yield ac
        app.dependency_overrides.pop(get_exchange_credential_service, None)
        app.dependency_overrides.pop(get_credential_resolver, None)


def _unique_email() -> str:
    return f"test-{uuid.uuid4().hex}@example.com"


async def _register_user(client) -> dict:
    email = _unique_email()
    response = await client.post(
        "/auth/register", json={"email": email, "password": STRONG_PASSWORD}
    )
    token = response.json()["data"]["access_token"]
    return {"Authorization": f"Bearer {token}"}


async def test_register_credential_succeeds(client):
    headers = await _register_user(client)

    response = await client.post(
        "/exchange-credentials",
        json={"exchange": "bitget", "api_key": "good-key", "api_secret": "secret"},
        headers=headers,
    )

    assert response.status_code == 201
    body = response.json()
    assert body["exchange"] == "bitget"
    assert body["is_active"] is True
    assert body["withdrawal_permission_warning"] is not None


async def test_register_credential_rejects_invalid_keys(client):
    headers = await _register_user(client)

    response = await client.post(
        "/exchange-credentials",
        json={"exchange": "bitget", "api_key": "bad-key", "api_secret": "secret"},
        headers=headers,
    )

    assert response.status_code == 400


async def test_list_credentials_after_register(client):
    headers = await _register_user(client)
    await client.post(
        "/exchange-credentials",
        json={"exchange": "bitget", "api_key": "good-key", "api_secret": "secret"},
        headers=headers,
    )

    response = await client.get("/exchange-credentials", headers=headers)

    assert response.status_code == 200
    assert any(c["exchange"] == "bitget" for c in response.json())


async def test_revoke_credential(client):
    headers = await _register_user(client)
    await client.post(
        "/exchange-credentials",
        json={"exchange": "bitget", "api_key": "good-key", "api_secret": "secret"},
        headers=headers,
    )

    response = await client.delete("/exchange-credentials/bitget", headers=headers)

    assert response.status_code == 200
    list_response = await client.get("/exchange-credentials", headers=headers)
    active = [c for c in list_response.json() if c["exchange"] == "bitget"]
    assert active == [] or active[0]["is_active"] is False


async def test_revoke_nonexistent_credential_returns_404(client):
    headers = await _register_user(client)

    response = await client.delete("/exchange-credentials/bitget", headers=headers)

    assert response.status_code == 404


async def test_get_balance_for_unlinked_exchange_returns_404(client):
    headers = await _register_user(client)

    response = await client.get("/exchange-credentials/bitget/balance", headers=headers)

    assert response.status_code == 404


async def test_get_balance_after_linking(client):
    headers = await _register_user(client)
    await client.post(
        "/exchange-credentials",
        json={"exchange": "bitget", "api_key": "good-key", "api_secret": "secret"},
        headers=headers,
    )

    response = await client.get("/exchange-credentials/bitget/balance", headers=headers)

    assert response.status_code == 200
    assert response.json() == []


async def test_get_capabilities_after_linking(client):
    headers = await _register_user(client)
    await client.post(
        "/exchange-credentials",
        json={"exchange": "bitget", "api_key": "good-key", "api_secret": "secret"},
        headers=headers,
    )

    response = await client.get("/exchange-credentials/bitget/capabilities", headers=headers)

    assert response.status_code == 200
    assert response.json()["exchange_name"] == "bitget"


async def test_credentials_require_authentication(client):
    response = await client.get("/exchange-credentials")

    assert response.status_code == 401


async def test_register_and_revoke_invalidate_resolver_cache():
    """docs/RED_TEAM_FINDINGS.md #02 회귀 — 캐시가 실제 싱글턴이 된 이상,
    재등록/해지 직후 옛 자격증명으로 만든 어댑터가 TTL 동안 계속 쓰이지
    않으려면 라우터가 반드시 invalidate()를 호출해야 한다."""
    invalidated: list[tuple] = []

    class _TrackingResolver(CredentialResolver):
        def invalidate(self, user_id, exchange):
            invalidated.append((user_id, exchange))
            super().invalidate(user_id, exchange)

    async def _override_tracking_resolver(pool=Depends(get_pool)):
        service = ExchangeCredentialService(pool, key_ring=KEY_RING, adapter_factory=_fake_factory)
        return _TrackingResolver(service, adapter_factory=_fake_factory)

    async with app.router.lifespan_context(app):
        app.dependency_overrides[get_exchange_credential_service] = _override_credential_service
        app.dependency_overrides[get_credential_resolver] = _override_tracking_resolver
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            headers = await _register_user(ac)
            register_response = await ac.post(
                "/exchange-credentials",
                json={"exchange": "bitget", "api_key": "good-key", "api_secret": "secret"},
                headers=headers,
            )
            assert register_response.status_code == 201
            revoke_response = await ac.delete("/exchange-credentials/bitget", headers=headers)
            assert revoke_response.status_code == 200
        app.dependency_overrides.pop(get_exchange_credential_service, None)
        app.dependency_overrides.pop(get_credential_resolver, None)

    assert len(invalidated) == 2
    assert all(exchange == "bitget" for _, exchange in invalidated)


async def test_credential_resolver_is_a_real_singleton_across_requests():
    """docs/RED_TEAM_FINDINGS.md #02 회귀 — get_credential_resolver()가 매
    요청 CredentialResolver(credential_service)를 새로 만들면 내부 5분
    TTL _cache가 매번 빈 채로 시작해 캐시가 한 번도 실제로 작동한 적이
    없었다. main.py lifespan이 app.state에 한 번만 만들어 둔 인스턴스를
    그대로 재사용하는지 직접 확인한다(이 테스트는 client fixture의 가짜
    resolver 오버라이드를 쓰지 않는다 — 실제 배선을 검증해야 하므로)."""

    class _FakeRequest:
        def __init__(self, app):
            self.app = app

    async with app.router.lifespan_context(app):
        first = get_credential_resolver(_FakeRequest(app))
        second = get_credential_resolver(_FakeRequest(app))
        assert first is second
        assert first is app.state.credential_resolver


async def test_get_balance_fails_closed_when_key_ring_rotates_after_registration():
    """실패 주입 — 등록 시 쓴 키 링과 다른 키 링으로 복호화를 시도하면
    (예: `KMS_KEY_ID` 로테이션 직후 캐시 미스) `ExchangeCredentialService
    .get_decrypted`가 `ExchangeCredentialDecryptionError`를 던지고
    (src/services/exchange_credential_service.py:239-240), 전역 예외
    등록(`exception_registry.py:108`)이 이를 400/VALIDATION_INVALID_FIELD로
    매핑한다 — 평문이 새 나가거나 500으로 죽지 않고 fail-closed로 거부되는지
    엔드투엔드로 증명한다."""

    async def _override_register_service(pool=Depends(get_pool)):
        return ExchangeCredentialService(pool, key_ring=KEY_RING, adapter_factory=_fake_factory)

    async def _override_rotated_resolver(pool=Depends(get_pool)):
        service = ExchangeCredentialService(
            pool, key_ring=_ROTATED_KEY_RING, adapter_factory=_fake_factory
        )
        return CredentialResolver(service, adapter_factory=_fake_factory)

    async with app.router.lifespan_context(app):
        app.dependency_overrides[get_exchange_credential_service] = _override_register_service
        app.dependency_overrides[get_credential_resolver] = _override_rotated_resolver
        transport = ASGITransport(app=app, raise_app_exceptions=False)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            headers = await _register_user(ac)
            register_response = await ac.post(
                "/exchange-credentials",
                json={"exchange": "bitget", "api_key": "good-key", "api_secret": "secret"},
                headers=headers,
            )
            assert register_response.status_code == 201

            response = await ac.get("/exchange-credentials/bitget/balance", headers=headers)

        app.dependency_overrides.pop(get_exchange_credential_service, None)
        app.dependency_overrides.pop(get_credential_resolver, None)

    assert response.status_code == 400
    assert "api_secret" not in response.text
    assert "good-key" not in response.text


@pytest.mark.perf
async def test_list_credentials_p95_latency_within_budget(client):
    """성능 단언 — `list_credentials`는 top-level 배열을 반환하는 단순 조회
    경로다(파일 상단 docstring이 지적하듯 아직 `ApiResponse` 봉투가 아니다).
    ADR-2026-09-09-C의 축별 예산표에 이 축 전용 항목이 없어(모듈 상단 주석
    참조) 가장 가까운 조회 예산을 차용해 회귀를 고정한다."""
    headers = await _register_user(client)
    await client.post(
        "/exchange-credentials",
        json={"exchange": "bitget", "api_key": "good-key", "api_secret": "secret"},
        headers=headers,
    )

    samples: list[float] = []
    for _ in range(10):
        started = time.perf_counter()
        response = await client.get("/exchange-credentials", headers=headers)
        samples.append(time.perf_counter() - started)
        assert response.status_code == 200

    ordered = sorted(samples)
    p95_index = max(0, math.ceil(0.95 * len(ordered)) - 1)
    assert ordered[p95_index] < _LIST_CREDENTIALS_P95_BUDGET_SECONDS


def _load_v1_snapshot() -> dict | None:
    if not _V1_SNAPSHOT_PATH.exists():
        return None
    return json.loads(_V1_SNAPSHOT_PATH.read_text(encoding="utf-8"))


def test_gate_red_repro_envelope_wrapping_flips_openapi_compat_to_major_violation():
    """게이트 적색 재현 — 이 파일 상단 docstring(PLT-17 decision, line
    12-22)은 "여기서 감싸면 contracts/openapi/v1.json 베이스라인 대비 MAJOR
    위반이 발생해 check_openapi_compat.py가 FAIL한다"고 주장하지만, 그
    주장이 실제 게이트 함수(`find_violations`) 동작과 일치하는지 지금까지
    검증한 적이 없었다. POST /exchange-credentials 201 응답을 baseline에서
    `ApiResponse` 봉투(`{"data": ..., "meta": ...}`)로 감싼 "current" 스냅샷을
    합성해, 그 결과가 실제로 `response property 제거` MAJOR 위반으로
    잡히는지 직접 재현한다 — PM의 needs_decision 보류가 근거 없는 추측이
    아니라 기계로 확인 가능한 사실임을 고정한다."""
    baseline = _load_v1_snapshot()
    if baseline is None:
        return
    credential_response = baseline["components"]["schemas"]["CredentialResponse"]

    current = copy.deepcopy(baseline)
    current["paths"]["/exchange-credentials"]["post"]["responses"]["201"]["content"][
        "application/json"
    ]["schema"] = {
        "type": "object",
        "properties": {
            "data": {"$ref": "#/components/schemas/CredentialResponse"},
            "meta": {"type": "object"},
        },
    }

    violations = find_violations(baseline, current)

    property_removed = [
        v
        for v in violations
        if v.startswith("response property 제거:")
        and "POST /exchange-credentials 201 response ." in v
    ]
    assert len(property_removed) == len(credential_response["properties"]), violations
