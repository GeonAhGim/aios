"""74번 §7 rollout gate 1단계 "fake provider" — 실 provider 통합은 이 리프의
스콥이 아니다(71번 §6, 74번 §7 "This specification does not authorize trading
integration"). 테스트와, 실 provider가 승인되기 전까지의 유일한 어댑터.
"""
from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

from src.foundation.connections.domain.models import (
    CapabilityScope,
    ProviderSnapshot,
    ScopeProof,
    SnapshotValue,
)
from src.foundation.connections.ports.provider import OpaqueRef, SecretLease


class FakeReadonlyAccountProvider:
    """`granted_scopes`를 생성 시점에 고정해 scope drift(CON-002 계열) 테스트를
    결정적으로 만든다 — 기본값은 요청 스코프를 그대로 승인하는 정상 경로."""

    def __init__(
        self,
        *,
        granted_scopes: tuple[CapabilityScope, ...] = (
            CapabilityScope.READ_BALANCE,
            CapabilityScope.READ_POSITION,
            CapabilityScope.READ_ACTIVITY,
        ),
        fail_verification: bool = False,
        fail_fetch: bool = False,
        snapshot_values: tuple[SnapshotValue, ...] = (),
    ) -> None:
        self._granted_scopes = granted_scopes
        self._fail_verification = fail_verification
        self._fail_fetch = fail_fetch
        self._snapshot_values = snapshot_values

    async def verify_readonly_scope(self, lease: SecretLease) -> ScopeProof:
        if self._fail_verification:
            raise ConnectionError("fake provider: scope verification 실패(시뮬레이션)")
        return ScopeProof(
            granted_scopes=self._granted_scopes,
            provider_credential_ref=f"fake-cred-{uuid4().hex[:8]}",
            provider_verified=True,
        )

    async def fetch_snapshot(self, account_ref: OpaqueRef, as_of: datetime) -> ProviderSnapshot:
        if self._fail_fetch:
            raise ConnectionError("fake provider: snapshot fetch 실패(시뮬레이션)")
        return ProviderSnapshot(
            provider_as_of=datetime.now(timezone.utc),
            currency="USD",
            raw_payload_ref=f"fake-payload-{uuid4().hex[:8]}",
            values=self._snapshot_values,
        )
