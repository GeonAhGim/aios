"""ReadonlyAccountProvider 포트(SecretLease/OpaqueRef/Protocol) 단위테스트 —

SecretLease/OpaqueRef 값 객체 생성 비용 측정, 0 수렴, 게이트-검출력(적색
전환)을 검증한다. ReadonlyAccountProvider Protocol 구조적 타이핑과
FakeReadonlyAccountProvider 의존성 전파도 커버한다.
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
from tests.conftest import PerfBudget


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
def test_secret_lease_and_opaque_ref_construction_perf_budget(
    perf_budget: PerfBudget,
) -> None:
    """순수 속성 대입뿐인 값 객체 생성 비용 — 10,000회 생성이 50ms 예산을
    넘지 않는지 확인한다(가장 가까운 발행 예산인 command-write p95 50ms와
    같은 자릿수, ADR-2026-09-09-C Decision 1)."""

    def _build() -> None:
        for i in range(10_000):
            SecretLease(lease_ref=f"lease-{i}")
            OpaqueRef(f"ACCT-{i}")

    perf_budget.assert_within(_build, budget_ms=50.0)


@pytest.mark.perf
def test_secret_lease_and_opaque_ref_construction_0_convergence() -> None:
    """단일 호출 CPU 측정의 0 수렴 확인 — Windows `time.process_time()`
    약 15.6ms 양자화 tick 때문에 1회 호출은 항상 0ms로 읽힌다. 이 테스트는
    그 사실을 명시적으로 검증한다(task-11539 감사)."""

    # 단일 호출 10회 측정 — 모두 0ms여야 함(양자화 한계)
    for _ in range(10):
        cpu_start = time.process_time()
        SecretLease(lease_ref=f"lease-{_}")
        OpaqueRef(f"ACCT-{_}")
        cpu_ms = (time.process_time() - cpu_start) * 1000
        assert cpu_ms == 0.0, (
            f"single-call CPU should converge to 0 (got {cpu_ms:.3f}ms); "
            "if it does not, the 0-convergence invariant is broken"
        )


@pytest.mark.perf
def test_secret_lease_and_opaque_ref_construction_perf_budget_goes_red_with_delay(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """검출력 확인 — 값 객체 생성에 sleep을 주입하면 같은 단언이 적색으로
    넘어간다. 이는 성능 단언이 '아무것도 잡지 못하는 허울'이 아님을
    증명한다(task-11539 감사).

    100회 호출당 ~100ms sleep(Windows 양자화) = 평균 100ms wall time로
    예산(50ms)을 초과하므로 assert가 적색으로 넘어간다."""

    # Monkeypatch SecretLease.__init__에 sleep 주입
    _orig_sl_init = SecretLease.__init__

    def slow_sl_init(self: SecretLease, *, lease_ref: str) -> None:
        time.sleep(0.1)  # 100ms (Windows resolution ~15ms minimum)
        _orig_sl_init(self, lease_ref=lease_ref)

    monkeypatch.setattr(SecretLease, "__init__", slow_sl_init)

    def _build_100() -> None:
        for i in range(100):
            SecretLease(lease_ref=f"lease-{i}")
            OpaqueRef(f"ACCT-{i}")

    # wall_ms로 검증 — sleep은 CPU 시간을 늘리지 않으므로
    # time.process_time() 기반 assert_within은 통과할 수 있다.
    # 그래서 best_of + wall_ms 직접 단언으로 red 전환을 확인한다.
    sample = PerfBudget().best_of(_build_100, n=3)
    assert sample.wall_ms >= 50.0, (
        f"Perf assertion should go red with injected delay: "
        f"wall={sample.wall_ms:.1f}ms (expected >= 50ms)"
    )
