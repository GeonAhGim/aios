"""EO-01 순수 규칙 단위테스트 — DB 없음.

Spec: docs/specs/L4_execution_ownership_and_safety_gate_wiring_v1.0.md §8
"단위(순수 규칙): is_lease_available() — 없음/만료/동일소유자/타인점유
4개 케이스" + naive datetime 거부 negative test.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from src.foundation.execution_ownership.domain.models import ExecutionLease
from src.foundation.execution_ownership.domain.rules import is_lease_available

_NOW = datetime(2026, 9, 4, 12, 0, 0, tzinfo=timezone.utc)


def _lease(owner_id: str, expires_at: datetime) -> ExecutionLease:
    return ExecutionLease(
        execution_id=1,
        owner_id=owner_id,
        fencing_token=0,
        heartbeat_at=_NOW - timedelta(seconds=1),
        expires_at=expires_at,
    )


def test_is_lease_available_no_existing_lease():
    assert is_lease_available(None, now=_NOW, requesting_owner="worker-a") is True


def test_is_lease_available_expired_lease():
    existing = _lease("worker-b", expires_at=_NOW - timedelta(seconds=1))
    assert is_lease_available(existing, now=_NOW, requesting_owner="worker-a") is True


def test_is_lease_available_same_owner():
    existing = _lease("worker-a", expires_at=_NOW + timedelta(seconds=30))
    assert is_lease_available(existing, now=_NOW, requesting_owner="worker-a") is True


def test_is_lease_available_other_owner_not_expired():
    existing = _lease("worker-b", expires_at=_NOW + timedelta(seconds=30))
    assert is_lease_available(existing, now=_NOW, requesting_owner="worker-a") is False


def test_is_lease_available_other_owner_expires_exactly_now_is_still_held():
    # §5.1 SQL은 `expires_at < now()`(strict)로 만료를 판정한다 — 경계값에서
    # 순수 규칙이 DB와 다르게 "획득 가능"이라 답하면 안 된다.
    existing = _lease("worker-b", expires_at=_NOW)
    assert is_lease_available(existing, now=_NOW, requesting_owner="worker-a") is False


def test_is_lease_available_rejects_naive_now():
    existing = _lease("worker-b", expires_at=_NOW + timedelta(seconds=30))
    with pytest.raises(ValueError):
        is_lease_available(
            existing,
            now=datetime(2026, 9, 4, 12, 0, 0),
            requesting_owner="worker-a",
        )


def test_execution_lease_rejects_naive_heartbeat_at():
    with pytest.raises(ValueError):
        ExecutionLease(
            execution_id=1,
            owner_id="worker-a",
            fencing_token=0,
            heartbeat_at=datetime(2026, 9, 4, 12, 0, 0),
            expires_at=_NOW,
        )


def test_execution_lease_rejects_naive_expires_at():
    with pytest.raises(ValueError):
        ExecutionLease(
            execution_id=1,
            owner_id="worker-a",
            fencing_token=0,
            heartbeat_at=_NOW,
            expires_at=datetime(2026, 9, 4, 12, 0, 0),
        )
