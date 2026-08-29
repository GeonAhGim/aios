"""16번대 통합테스트 — /exchange-credentials 라우터. 실제 FastAPI 앱 + 실제
dev DB. 실제 Bitget/KIS Demo 키가 없어(.env 비어있음) FastAPI
dependency_overrides로 가짜 adapter_factory를 주입한다(이 세션에서
반복 적용한 DI 패턴을 라우터 계층에도 그대로 적용)."""
import uuid

import pytest
from fastapi import Depends
from httpx import ASGITransport, AsyncClient

from src.api.deps import get_pool
from src.api.service_deps import get_credential_resolver, get_exchange_credential_service
from src.exchanges.common.types import ExchangeCapability
from src.main import app
from src.services.credential_resolver import CredentialResolver
from src.services.exchange_credential_service import ExchangeCredentialService

STRONG_PASSWORD = "Str0ng!Passw0rd"
ENCRYPTION_KEY = "44" * 32


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
    return ExchangeCredentialService(
        pool, encryption_key=ENCRYPTION_KEY, adapter_factory=_fake_factory
    )


async def _override_resolver(pool=Depends(get_pool)):
    service = ExchangeCredentialService(
        pool, encryption_key=ENCRYPTION_KEY, adapter_factory=_fake_factory
    )
    return CredentialResolver(service, adapter_factory=_fake_factory)


@pytest.fixture
async def client():
    async with app.router.lifespan_context(app):
        app.dependency_overrides[get_exchange_credential_service] = _override_credential_service
        app.dependency_overrides[get_credential_resolver] = _override_resolver
        transport = ASGITransport(app=app)
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
    token = response.json()["access_token"]
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
        service = ExchangeCredentialService(
            pool, encryption_key=ENCRYPTION_KEY, adapter_factory=_fake_factory
        )
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
