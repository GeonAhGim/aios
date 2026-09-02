"""74번 §3 ReadonlyAccountProvider — 이 Protocol에는 주문/이체/출금/서명/원문
자격증명 조회를 위한 메서드가 하나도 없다(CON-009가 이걸 계약 테스트로
고정한다). 실 provider 통합은 71번 §6 "provider/legal review 후 결정" —
이 리프는 fake adapter(adapters/fake_provider.py)만 구현한다."""
from __future__ import annotations

from datetime import datetime
from typing import Protocol

from src.foundation.connections.domain.models import ProviderSnapshot, ScopeProof


class SecretLease:
    """vault에서 짧게 빌린 자격증명 핸들 — provider 어댑터 밖으로 원문이
    나가지 않는다(74번 §3 "acquire short-lived vault lease")."""

    def __init__(self, lease_ref: str) -> None:
        self.lease_ref = lease_ref


class OpaqueRef:
    def __init__(self, value: str) -> None:
        self.value = value


class ReadonlyAccountProvider(Protocol):
    async def verify_readonly_scope(self, lease: SecretLease) -> ScopeProof: ...

    async def fetch_snapshot(
        self, account_ref: OpaqueRef, as_of: datetime
    ) -> ProviderSnapshot: ...
