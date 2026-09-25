"""FakeReadonlyAccountProvider 단위테스트 — task-4667 커버리지 0% 보강.

이 어댑터는 실 거래소 연동이 아니라 CON-002 시리즈(scope drift) 등을 결정론적으로
재현하기 위한 테스트 전용 더블이다(파일 docstring 참조). 별도의 게이트/불변식을
방어하는 코드가 아니므로 red-gate 재현은 N/A(테스트 더블에 우회할 게이트가 없음) —
CLAUDE.md §5 / ADR-2026-09-10-C Decision 4.
"""

from __future__ import annotations

import time
from datetime import datetime, timezone
from decimal import Decimal

import pytest

from src.foundation.connections.adapters.fake_provider import FakeReadonlyAccountProvider
from src.foundation.connections.domain.models import CapabilityScope, SnapshotValue
from src.foundation.connections.ports.provider import OpaqueRef, SecretLease


async def test_verify_readonly_scope_defaults_to_full_p0_profile():
    provider = FakeReadonlyAccountProvider()

    proof = await provider.verify_readonly_scope(SecretLease(lease_ref="lease-1"))

    assert proof.granted_scopes == (
        CapabilityScope.READ_BALANCE,
        CapabilityScope.READ_POSITION,
        CapabilityScope.READ_ACTIVITY,
    )
    assert proof.provider_verified is True
    assert proof.provider_credential_ref.startswith("fake-cred-")


async def test_verify_readonly_scope_honors_custom_granted_scopes():
    """CON-002 시리즈(scope drift)를 결정론적으로 재현하려면 요청과 다른
    승인 스코프를 고정할 수 있어야 한다 — 생성자 인자가 실제로 반영되는지 확인."""
    provider = FakeReadonlyAccountProvider(granted_scopes=(CapabilityScope.READ_BALANCE,))

    proof = await provider.verify_readonly_scope(SecretLease(lease_ref="lease-1"))

    assert proof.granted_scopes == (CapabilityScope.READ_BALANCE,)


async def test_verify_readonly_scope_with_empty_scopes_boundary():
    """경계값 — 승인 스코프가 빈 튜플이어도 예외 없이 그대로 반환한다."""
    provider = FakeReadonlyAccountProvider(granted_scopes=())

    proof = await provider.verify_readonly_scope(SecretLease(lease_ref="lease-1"))

    assert proof.granted_scopes == ()


async def test_verify_readonly_scope_fails_when_configured(monkeypatch: pytest.MonkeyPatch):
    """실패주입 — fail_verification=True는 의존성(provider) 오류를 시뮬레이션한다."""
    provider = FakeReadonlyAccountProvider(fail_verification=True)

    with pytest.raises(ConnectionError, match="scope verification"):
        await provider.verify_readonly_scope(SecretLease(lease_ref="lease-1"))


async def test_fetch_snapshot_fails_when_configured():
    """실패주입 — fail_fetch=True는 스냅샷 조회 실패를 시뮬레이션한다."""
    provider = FakeReadonlyAccountProvider(fail_fetch=True)

    with pytest.raises(ConnectionError, match="snapshot fetch"):
        await provider.fetch_snapshot(OpaqueRef("ACCT-1"), datetime.now(timezone.utc))


async def test_fetch_snapshot_returns_configured_values():
    values = (SnapshotValue(entity_type="BALANCE", entity_key="USDT", value=Decimal("100.5")),)
    provider = FakeReadonlyAccountProvider(snapshot_values=values)

    snapshot = await provider.fetch_snapshot(OpaqueRef("ACCT-1"), datetime.now(timezone.utc))

    assert snapshot.values == values
    assert snapshot.currency == "USD"
    assert snapshot.raw_payload_ref.startswith("fake-payload-")


async def test_fetch_snapshot_defaults_to_empty_values_boundary():
    """경계값 — snapshot_values를 지정하지 않으면 빈 튜플이 그대로 반환된다."""
    provider = FakeReadonlyAccountProvider()

    snapshot = await provider.fetch_snapshot(OpaqueRef("ACCT-1"), datetime.now(timezone.utc))

    assert snapshot.values == ()


# ---------------------------------------------------------------------------
# perf assertion (D2)
# ---------------------------------------------------------------------------


@pytest.mark.perf
async def test_fetch_snapshot_perf_budget_p95_latency() -> None:
    """발행된 FND-05 전용 예산이 없어(ADR-2026-09-09-C Decision 1 표) 가장
    가까운 publish된 command-write 예산("order submit -> ACK p95 50ms, paper")과
    같은 자릿수를 사용한다 — 이 더블도 메모리 내에서만 동작하는 단일 조회다."""
    samples = 50
    durations_ms: list[float] = []
    provider = FakeReadonlyAccountProvider()

    for _ in range(samples):
        start = time.perf_counter()
        await provider.fetch_snapshot(OpaqueRef("ACCT-1"), datetime.now(timezone.utc))
        durations_ms.append((time.perf_counter() - start) * 1000)

    durations_ms.sort()
    p95 = durations_ms[int(samples * 0.95) - 1]
    assert p95 < 50.0, f"fetch_snapshot p95 latency {p95:.3f}ms exceeded 50ms budget"
