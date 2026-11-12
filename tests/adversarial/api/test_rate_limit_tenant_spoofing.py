"""task-7989 (M2-16) — adversarial test: tenant rate-limit key spoofing.

Spec: docs/specs/L4_platform_observability_tenancy_api_v1.0.md#§9 PLT-25

`RateLimitMiddleware` runs pre-auth (module docstring in
`src/api/middleware/rate_limit.py`), so `_resolve_key`'s "tenant" branch used
to trust the unauthenticated `X-Tenant-Id` header outright. An anonymous
caller could forge `X-Tenant-Id: <victim>` against any `/admin/*` route
(`POLICIES["admin"]`, key="tenant", limit=30/60s) and exhaust the victim
tenant's admin bucket — a silent DoS where real admins of that tenant get
429. The "subject" branch already verified the bearer JWT's signature before
trusting `sub`; behind `AIOS_RATE_LIMIT_TENANT_JWT_VERIFY` (default OFF —
MVP-2 staged rollout), the "tenant" branch now requires the same
verification before trusting the header, falling back to an IP-keyed bucket
otherwise. No DB membership lookup is added — this is bucket separation, not
authorization.

These tests call `_resolve_key` directly against fabricated ASGI-scope
`Request` objects (no app lifespan, no DB) — the fastest, most direct way to
exercise the branch in isolation, and the same style the middleware's own
"subject" branch already relies on (pure function of headers + app.state).
"""

from __future__ import annotations

import time

import jwt
import pytest
from pydantic import SecretStr
from starlette.requests import Request

from src.api.middleware.rate_limit import FLAG_TENANT_JWT_VERIFY, _resolve_key
from src.core.rate_limit.limiter import InMemoryTokenBucket
from src.core.rate_limit.policy import POLICIES

_JWT_SECRET = "aios-test-only-jwt-secret-must-be-at-least-32-bytes"
_JWT_ALGORITHM = "HS256"


class _FakeSecrets:
    def __init__(self, key: str = _JWT_SECRET, algorithm: str = _JWT_ALGORITHM) -> None:
        self.jwt_secret_key = SecretStr(key)
        self.jwt_algorithm = algorithm


class _FakeAppState:
    def __init__(self) -> None:
        self.secrets = _FakeSecrets()


class _FakeApp:
    def __init__(self) -> None:
        self.state = _FakeAppState()


def _make_request(
    *,
    headers: dict[str, str] | None = None,
    client_host: str = "203.0.113.9",
) -> Request:
    raw_headers = [
        (key.lower().encode("latin-1"), value.encode("latin-1"))
        for key, value in (headers or {}).items()
    ]
    scope = {
        "type": "http",
        "method": "GET",
        "path": "/admin/tenants",
        "headers": raw_headers,
        "client": (client_host, 51234),
        "app": _FakeApp(),
    }
    return Request(scope)


def _valid_jwt(sub: str = "admin-user-1") -> str:
    payload = {"sub": sub, "exp": time.time() + 3600}
    return jwt.encode(payload, _JWT_SECRET, algorithm=_JWT_ALGORITHM)


def _expired_jwt(sub: str = "admin-user-1") -> str:
    payload = {"sub": sub, "exp": time.time() - 3600}
    return jwt.encode(payload, _JWT_SECRET, algorithm=_JWT_ALGORITHM)


@pytest.fixture(autouse=True)
def _flag_on(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(FLAG_TENANT_JWT_VERIFY, "1")


def test_forged_tenant_header_without_bearer_falls_back_to_ip_key() -> None:
    """(a) An unauthenticated /admin/* request with a forged `X-Tenant-Id`
    does not get the "tenant:<id>" bucket key — it falls back to "ip:...",
    exactly like the "subject" branch does for an anonymous caller."""
    request = _make_request(headers={"X-Tenant-Id": "victim-tenant"}, client_host="198.51.100.1")

    key = _resolve_key(request, POLICIES["admin"])

    assert key == "ip:198.51.100.1"
    assert not key.startswith("tenant:")


async def test_forged_requests_do_not_exhaust_victim_tenant_bucket() -> None:
    """(b) Storm of >30 forged requests (no valid bearer, spoofed
    `X-Tenant-Id: victim-tenant`) must not consume the victim tenant's
    admin bucket -- a subsequent validly-authenticated admin request for
    that same tenant must still be allowed."""
    policy = POLICIES["admin"]
    bucket = InMemoryTokenBucket(clock=time.monotonic)

    for _ in range(policy.limit + 10):
        forged = _make_request(headers={"X-Tenant-Id": "victim-tenant"}, client_host="198.51.100.1")
        key = _resolve_key(forged, policy)
        assert key == "ip:198.51.100.1"
        await bucket.acquire(policy, key)

    legit_request = _make_request(
        headers={
            "X-Tenant-Id": "victim-tenant",
            "Authorization": f"Bearer {_valid_jwt()}",
        },
        client_host="10.0.0.1",
    )
    legit_key = _resolve_key(legit_request, policy)

    assert legit_key == "tenant:victim-tenant"
    decision = await bucket.acquire(policy, legit_key)
    assert decision.allowed


def test_authenticated_admin_request_still_buckets_per_tenant() -> None:
    """(c) A request with a validly-signed bearer JWT plus `X-Tenant-Id`
    still keys the bucket by tenant (no DB membership check is added --
    this only requires *a* valid signature, not that the token belongs to
    that tenant)."""
    request = _make_request(
        headers={
            "X-Tenant-Id": "real-tenant",
            "Authorization": f"Bearer {_valid_jwt()}",
        }
    )

    key = _resolve_key(request, POLICIES["admin"])

    assert key == "tenant:real-tenant"


_WRONG_SIGNATURE_JWT = jwt.encode(
    {"sub": "x"}, "wrong-secret-wrong-secret-wrong-enough", algorithm=_JWT_ALGORITHM
)


@pytest.mark.parametrize(
    "auth_header",
    [
        f"Bearer {_expired_jwt()}",
        "Bearer not-a-real-token.junk.payload",
        f"Bearer {_WRONG_SIGNATURE_JWT}",
    ],
    ids=["expired", "malformed", "wrong-signature"],
)
def test_invalid_signature_falls_back_to_ip_key(auth_header: str) -> None:
    """(d) An expired, malformed, or wrongly-signed bearer token is treated
    the same as no token at all -- fall back to the IP-keyed bucket."""
    request = _make_request(
        headers={"X-Tenant-Id": "victim-tenant", "Authorization": auth_header},
        client_host="198.51.100.7",
    )

    key = _resolve_key(request, POLICIES["admin"])

    assert key == "ip:198.51.100.7"


def test_flag_off_keeps_legacy_unverified_tenant_header_behavior(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """(e) With the flag OFF, current behavior is unchanged: an
    unauthenticated request with a forged `X-Tenant-Id` still keys the
    bucket by that raw header value."""
    monkeypatch.delenv(FLAG_TENANT_JWT_VERIFY, raising=False)
    request = _make_request(headers={"X-Tenant-Id": "victim-tenant"})

    key = _resolve_key(request, POLICIES["admin"])

    assert key == "tenant:victim-tenant"


# ── Performance tests (D2) ──────────────────────────────────────────────────────


@pytest.mark.perf
def test_resolve_key_latency_within_budget(perf_budget) -> None:
    """성능 단언(D2) — `_resolve_key` 1회 호출의 CPU 지연이 예산 이내여야 한다.

    실측(2026-11-12, local host, best-of-5 min): cpu=0.045ms.
    ADR-2026-09-09-C Decision 1 테이블에 없는 신규 경로이므로
    실측의 30배 여유로 예산 신설: 0.045ms x 30 = 1.35ms -> 1.0ms로
    안전 마진 확보. `_resolve_key`는 순수 동기 JWT 검증 + 문자열
    결합이므로 1ms는 충분히 여유 있다.
    """
    request = _make_request(
        headers={
            "X-Tenant-Id": "perf-tenant",
            "Authorization": f"Bearer {_valid_jwt()}",
        }
    )

    sample = perf_budget.best_of(lambda: _resolve_key(request, POLICIES["admin"]), n=5, warmup=1)

    assert sample.cpu_ms < 1.0, (
        f"_resolve_key latency {sample.cpu_ms:.3f}ms exceeded 1.0ms budget "
        f"(wall={sample.wall_ms:.3f}ms) — key resolution regressed"
    )


@pytest.mark.perf
def test_resolve_key_gate_red_goes_red_with_virtual_jwt_delay(
    perf_budget,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """게이트 적색 재현(D2): `jwt.decode`에 가상 지연 주입 시 성능 단언이
    적색(AssertionError)이어야 한다.

    FLAG_TENANT_JWT_VERIFY=1 상태에서 _resolve_key() 가 _verified_bearer_payload()
    를 거쳐 jwt.decode() 를 호출하는 경로에 50ms 지연을 주입한다.
    `_resolve_key`가 그 지연을 그대로 흡수하여 wall_ms가 50ms를 넘어
    1.0ms 예산의 10배를 초과한다. 단언이 "제대로 작동하는지"를
    역으로 증명한다.
    """
    from tests.support.perf_inject import virtual_delayed_sync

    # FLAG_TENANT_JWT_VERIFY=1 — _verified_bearer_payload() 가 jwt.decode() 호출
    monkeypatch.setenv(FLAG_TENANT_JWT_VERIFY, "1")
    request = _make_request(
        headers={
            "X-Tenant-Id": "red-tenant",
            "Authorization": f"Bearer {_valid_jwt()}",
        }
    )

    original_decode = jwt.decode

    def delayed_decode(*args, **kwargs):
        return virtual_delayed_sync(original_decode, 50.0)(*args, **kwargs)

    monkeypatch.setattr("jwt.decode", delayed_decode)

    # 적색 주입 — jwt.decode에 50ms 지연
    sample_red = perf_budget.best_of(
        lambda: _resolve_key(request, POLICIES["admin"]), n=3, warmup=0
    )
    assert sample_red.wall_ms >= 50.0, (
        f"virtual delay injection failed: wall_ms={sample_red.wall_ms:.3f}ms "
        "did NOT absorb the 50ms injection — budget assertion may be hollow"
    )
    assert sample_red.cpu_ms >= 50.0, (
        f"virtual delay injection failed: cpu_ms={sample_red.cpu_ms:.3f}ms "
        "did NOT absorb the 50ms injection — budget assertion may be hollow"
    )
