"""EO-01 `ExecutionLease` 값객체 naive datetime 거부 단위테스트.

Spec: docs/specs/L4_execution_ownership_and_safety_gate_wiring_v1.0.md —
ExecutionLease는 heartbeat_at / expires_at 모두 tz-aware UTC여야 한다.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta, tzinfo

import pytest

from src.foundation.execution_ownership.domain.models import ExecutionLease

_NOW = datetime(2026, 9, 4, 12, 0, 0, tzinfo=UTC)


# ── naive datetime 거부 (negative test) ────────────────────────────────


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


# ── 적대적 tzinfo (failure injection) ──────────────────────────────────


class _BrokenUtcOffsetTzinfo(tzinfo):
    """`tzinfo is not None`이지만 `utcoffset()`이 None인 기형 tzinfo.

    적대적으로 조작하면(또는 잘못 구현된 커스텀 tzinfo로) `value.tzinfo is
    None` 검사 하나만으로는 걸러지지 않고, 이후 datetime 비교에서
    `TypeError: can't compare offset-naive and offset-aware datetimes`로
    제어되지 않은 채 새어나간다 — 값객체 생성 경로가 fail-closed(명확한
    ValueError)로 막아야 할 실패 주입 시나리오."""

    def utcoffset(self, dt: datetime | None) -> timedelta | None:
        return None

    def dst(self, dt: datetime | None) -> timedelta | None:
        return None

    def tzname(self, dt: datetime | None) -> str | None:
        return None


def test_execution_lease_rejects_adversarial_tzinfo_with_none_utcoffset():
    # 위와 동일한 적대적 tzinfo를 값객체 생성 경로(__post_init__)에도 주입한다.
    adversarial = datetime(2026, 9, 4, 12, 0, 0, tzinfo=_BrokenUtcOffsetTzinfo())
    with pytest.raises(ValueError):
        ExecutionLease(
            execution_id=1,
            owner_id="worker-a",
            fencing_token=0,
            heartbeat_at=adversarial,
            expires_at=_NOW,
        )
