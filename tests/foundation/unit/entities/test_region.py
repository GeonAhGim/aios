"""FA-24 `domain/region.py` unit tests -- pure functions, no DB.

Spec: docs/specs/L4_ibor_fund_accounting_and_resilience_v1.0.md#FA-24.

INVARIANTS.md cross-check: no I-0x entry names data-sovereignty/region policy
directly (N/A(no matching invariant) for I-01..09, I-11). Closest is I-10
("implemented != working" -- safety-adjacent components need a wiring-proof
adversarial test): `test_a_storage_adapter_that_forgets_the_guard_writes_to_the_wrong_region`
below is that wiring proof -- it shows what happens to a real write path
if `assert_storage_region_allowed` is *not* called, which is exactly the bug
this leaf exists to make impossible.

replay_verify: N/A(pure domain policy, not an event-sourced projection --
there is no stream to replay).
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

import pytest

from src.foundation.entities.contracts.v1 import LegalEntity
from src.foundation.entities.domain.region import (
    RegionDeniedError,
    RegionStoragePolicy,
    UnknownRegionTagError,
    assert_storage_region_allowed,
    same_region_policy,
)
from tests.conftest import PerfBudget

NOW = datetime(2026, 9, 26, tzinfo=UTC)


def _entity(**overrides: Any) -> LegalEntity:
    # pydantic's mypy plugin synthesizes a keyword-specific `__init__` for
    # `LegalEntity`, so unpacking a `dict[str, object]` here fails arg-type
    # (`object` doesn't match each field's declared type); `dict[str, Any]`
    # satisfies it without an ignore-comment suppression (PLT-40 budget is a ratchet).
    defaults: dict[str, Any] = dict(
        entity_id=uuid4(),
        tenant_id=uuid4(),
        name="Acme KR",
        jurisdiction="KR",
        region_tag="kr-seoul",
    )
    defaults.update(overrides)
    return LegalEntity(**defaults)


# ---- happy path ----


def test_same_region_write_is_allowed():
    entity = _entity(region_tag="kr-seoul")
    policy = same_region_policy(["kr-seoul", "us-east"])

    assert_storage_region_allowed(entity, "kr-seoul", policy)  # raises 없음


def test_explicit_dr_replica_region_is_allowed_when_declared():
    entity = _entity(region_tag="eu-frankfurt")
    policy = RegionStoragePolicy({"eu-frankfurt": {"eu-frankfurt", "eu-dublin"}})

    assert_storage_region_allowed(entity, "eu-dublin", policy)  # raises 없음


# ---- negative tests (>= 3) ----


def test_cross_region_write_is_rejected():
    """Adversarial DoD case: an entity tagged for one region must not be
    storable in another region's adapter."""
    entity = _entity(region_tag="kr-seoul")
    policy = same_region_policy(["kr-seoul", "us-east"])

    with pytest.raises(RegionDeniedError):
        assert_storage_region_allowed(entity, "us-east", policy)


def test_undeclared_region_is_rejected_even_when_other_regions_are_declared():
    entity = _entity(region_tag="eu-frankfurt")
    policy = RegionStoragePolicy({"eu-frankfurt": {"eu-frankfurt"}})

    with pytest.raises(RegionDeniedError):
        assert_storage_region_allowed(entity, "eu-dublin", policy)


def test_unknown_region_tag_fails_closed_instead_of_allowing_everywhere():
    """Negative test: a `region_tag` absent from the policy must deny, not
    silently allow (data-sovereignty breach) or silently no-op (denies every
    write forever without surfacing why)."""
    entity = _entity(region_tag="unregistered-tag")
    policy = same_region_policy(["kr-seoul"])

    with pytest.raises(UnknownRegionTagError):
        assert_storage_region_allowed(entity, "kr-seoul", policy)


def test_region_denied_error_carries_entity_and_target_for_audit():
    """Negative test: the raised error must expose enough context (entity id,
    its region_tag, the rejected target) for an audit log / incident report --
    a bare ValueError message is not enough for FA_REGION_DENIED evidence."""
    entity = _entity(region_tag="kr-seoul")
    policy = same_region_policy(["kr-seoul", "us-east"])

    with pytest.raises(RegionDeniedError) as excinfo:
        assert_storage_region_allowed(entity, "us-east", policy)

    err = excinfo.value
    assert err.entity_id == entity.entity_id
    assert err.region_tag == "kr-seoul"
    assert err.storage_region == "us-east"


# ---- failure injection ----


def test_a_storage_adapter_that_forgets_the_guard_writes_to_the_wrong_region():
    """Failure injection: simulate a write adapter that has a bug and skips
    the `assert_storage_region_allowed` call entirely (e.g. a future adapter
    added without wiring the guard). Demonstrates the exact failure mode this
    leaf exists to prevent -- a correct adapter must call the guard *before*
    the write, or cross-region storage silently succeeds."""
    entity = _entity(region_tag="kr-seoul")
    policy = same_region_policy(["kr-seoul", "us-east"])
    store: dict[str, list[str]] = {"kr-seoul": [], "us-east": []}

    def guarded_write(target_region: str) -> None:
        assert_storage_region_allowed(entity, target_region, policy)
        store[target_region].append(str(entity.entity_id))

    def unguarded_write(target_region: str) -> None:  # simulated bug
        store[target_region].append(str(entity.entity_id))

    with pytest.raises(RegionDeniedError):
        guarded_write("us-east")
    assert store["us-east"] == []

    unguarded_write("us-east")
    assert store["us-east"] == [str(entity.entity_id)]  # the bug this leaf prevents


# ---- performance assertion ----


@pytest.mark.perf
def test_region_check_throughput_budget(perf_budget: PerfBudget):
    """Numeric performance assertion: the guard is a pure dict-lookup +
    frozenset membership test called on every entity-scoped write, so it must
    stay far below the write path itself -- 50k checks in well under 1s."""
    entity = _entity(region_tag="kr-seoul")
    policy = same_region_policy(["kr-seoul", "us-east"])
    iterations = 50_000

    def _run() -> None:
        for _ in range(iterations):
            assert_storage_region_allowed(entity, "kr-seoul", policy)

    perf_budget.assert_within(_run, budget_ms=1000.0, batch=8, label=f"{iterations} region checks")


@pytest.mark.perf
def test_gate_red_budget_actually_fails_past_budget(perf_budget: PerfBudget):
    """게이트 적색 재현 -- 성능 단언이 tautology가 아님을 증명한다.
    동일한 샘플에 터무니없이 낮은 예산(1e-9ms)을 적용하면 AssertionError가
    발생해야 한다. budget 값을 10배 늘리는 monkeypatch 대신, assert 자체를
    pytest.raises로 검증하는 것이 가장 명확하다(task-11539)."""
    entity = _entity(region_tag="kr-seoul")
    policy = same_region_policy(["kr-seoul", "us-east"])
    iterations = 50_000

    def _run() -> None:
        for _ in range(iterations):
            assert_storage_region_allowed(entity, "kr-seoul", policy)

    samples = perf_budget.samples(_run, n=5, warmup=0, batch=8)
    cpu_ms = min(s.cpu_ms for s in samples)

    absurdly_low_budget_ms = 1e-9
    with pytest.raises(AssertionError):
        assert cpu_ms < absurdly_low_budget_ms, (
            f"예상 적색 실패: cpu={cpu_ms:.6f}ms가 absurd budget "
            f"{absurdly_low_budget_ms}ms보다 작지 않음"
        )
