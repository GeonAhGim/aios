"""ReadonlyAccountProvider 포트(SecretLease/OpaqueRef/Protocol) 단위테스트 —
task-4687 커버리지 보강.

이 파일은 순수 값 객체(SecretLease/OpaqueRef)와 구조적 타이핑 Protocol 정의만
담고 있어 우회할 게이트가 없다 — red-gate 재현은 N/A(포트 정의 자체에 게이트가
없음, FakeReadonlyAccountProvider 테스트와 동일한 근거) — CLAUDE.md §5 /
ADR-2026-09-10-C Decision 4.
"""

from __future__ import annotations

import time
from datetime import datetime, timezone

import pytest

from src.foundation.connections.adapters.fake_provider import FakeReadonlyAccountProvider
from src.foundation.connections.ports.provider import (
    OpaqueRef,
    ReadonlyAccountProvider,
    SecretLease,
)


def test_secret_lease_stores_lease_ref():
    lease = SecretLease(lease_ref="lease-1")

    assert lease.lease_ref == "lease-1"


def test_opaque_ref_stores_value():
    ref = OpaqueRef("ACCT-1")

    assert ref.value == "ACCT-1"


def test_secret_lease_empty_string_boundary():
    """경계값 — 빈 문자열 lease_ref도 예외 없이 그대로 저장된다(형식 검증은
    이 값 객체의 책임이 아니다)."""
    lease = SecretLease(lease_ref="")

    assert lease.lease_ref == ""


def test_opaque_ref_empty_string_boundary():
    """경계값 — 빈 문자열 value도 예외 없이 그대로 저장된다."""
    ref = OpaqueRef("")

    assert ref.value == ""


def test_readonly_account_provider_is_not_runtime_checkable():
    """부정 케이스 — ReadonlyAccountProvider는 @runtime_checkable로 선언돼
    있지 않으므로 isinstance() 검사는 TypeError로 실패해야 한다. 구조적
    타이핑(정적 검증)만 의도된 계약이라는 것을 고정한다."""
    provider = FakeReadonlyAccountProvider()

    with pytest.raises(TypeError):
        isinstance(provider, ReadonlyAccountProvider)


async def test_readonly_account_provider_protocol_propagates_injected_failure(
    monkeypatch: pytest.MonkeyPatch,
):
    """실패주입 — Protocol 타입으로 참조되는 구현체의 의존성 예외가 그대로
    전파되는지 monkeypatch로 확인한다."""
    provider: ReadonlyAccountProvider = FakeReadonlyAccountProvider()

    async def _boom(self: FakeReadonlyAccountProvider, lease: SecretLease):
        raise TimeoutError("simulated dependency timeout")

    monkeypatch.setattr(FakeReadonlyAccountProvider, "verify_readonly_scope", _boom)

    with pytest.raises(TimeoutError, match="simulated dependency timeout"):
        await provider.verify_readonly_scope(SecretLease(lease_ref="lease-1"))


async def test_readonly_account_provider_fetch_snapshot_conforms_to_protocol_signature():
    """양성 케이스 — Protocol 시그니처대로 구현체를 통해 호출 가능함을 확인한다."""
    provider: ReadonlyAccountProvider = FakeReadonlyAccountProvider()

    snapshot = await provider.fetch_snapshot(OpaqueRef("ACCT-1"), datetime.now(timezone.utc))

    assert snapshot.currency == "USD"


@pytest.mark.perf
def test_secret_lease_and_opaque_ref_construction_perf_budget():
    """순수 속성 대입뿐인 값 객체 생성 비용 — 10,000회 생성이 50ms 예산을
    넘지 않는지 확인한다(가장 가까운 발행 예산인 command-write p95 50ms와
    같은 자릿수, ADR-2026-09-09-C Decision 1)."""
    start = time.perf_counter()
    for i in range(10_000):
        SecretLease(lease_ref=f"lease-{i}")
        OpaqueRef(f"ACCT-{i}")
    elapsed_ms = (time.perf_counter() - start) * 1000

    assert elapsed_ms < 50.0, f"construction of 20,000 value objects took {elapsed_ms:.3f}ms"
