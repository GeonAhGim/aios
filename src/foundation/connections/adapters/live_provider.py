"""LiveReadonlyAccountProvider — 실 거래소 어댑터(legacy `CredentialResolver`)
위의 read-only `ReadonlyAccountProvider` 구현.

Spec: AIOSproject 74번 §3, 전수감사(agent-platform-12, docs/FULL_AUDIT_2026-09-02.md §6).

기존 자격증명 시스템 재사용 — FND-05 자신의 vault(74번 §3 "acquire short-lived
vault lease")는 아직 진짜 비밀을 다루지 않는다. `confirm_connection.py`가
`SecretLease(lease_ref=f"lease-{uuid4().hex}")`로 자리표시자만 만드는 이유가
그것이다 — 실 API 키는 legacy `CredentialResolver`(12.4, exchange_credentials
테이블)가 이미 갖고 있으므로, 새 vault를 만드는 대신 그걸 그대로 쓴다
(`src/api/routers/foundation/validation.py`가 이미 같은 방식으로 FND-04를
legacy 자격증명에 연결해둔 선례가 있다).

scope 검증의 한계 — `exchange_credential_service.py`가 이미 문서화한 것과
같은 제약: Bitget/KIS 어댑터 둘 다 API 키의 권한범위(READ_BALANCE 등 AIOS
분류)를 조회하는 엔드포인트를 구현하지 않는다(FD-13.3 "지원 안 하면 경고
문구로 대체, 거짓으로 확인됐다고 주장하지 않는다"). 이 어댑터도 같은
원칙을 따른다 — `verify_readonly_scope()`는 연결 자체를 막지 않기 위해
요청된 스코프를 그대로 승인된 것으로 다루되, `ScopeProof.provider_verified
=False`로 "이건 provider가 독립적으로 확인해준 게 아니다"를 정직하게
남긴다.

position — Bitget/KIS 두 어댑터 모두 spot 전용이라 `get_positions()`가
항상 빈 리스트를 반환하도록 이미 문서화돼 있다(account_mixin.py 참조,
"거래소가 AIOS의 전략별 컨텍스트를 모르므로"). 그래도 이 어댑터는 그
사실에 기대어 호출을 생략하지 않는다 — 그대로 호출해서 결과를 병합만
한다. 나중에 다른 거래소/계약 유형이 실제 값을 채우기 시작해도 이 코드는
바꿀 필요가 없다.
"""
from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID

from src.foundation.connections.domain.models import (
    CapabilityScope,
    ProviderSnapshot,
    ScopeProof,
    SnapshotValue,
)
from src.foundation.connections.ports.provider import OpaqueRef, SecretLease
from src.services.credential_resolver import CredentialResolver

_CURRENCY_FIELD_MAX_LEN = 10  # account_snapshot.currency VARCHAR(10)
_NO_BALANCE_CURRENCY = "MULTI"


class LiveReadonlyAccountProvider:
    def __init__(
        self,
        resolver: CredentialResolver,
        *,
        user_id: UUID,
        exchange: str,
        requested_capability_profile: tuple[CapabilityScope, ...],
    ) -> None:
        self._resolver = resolver
        self._user_id = user_id
        self._exchange = exchange
        self._requested_capability_profile = requested_capability_profile

    async def verify_readonly_scope(self, lease: SecretLease) -> ScopeProof:
        # get_adapter()가 CredentialNotFoundError를 던지면(등록 안 됨/해지됨)
        # 그대로 전파한다 — confirm_connection.py가 이미 모든 예외를
        # ScopeVerificationFailedError로 감싼다. get_balance() 호출 자체가
        # "이 키가 실제로 동작하는가"의 유일하고 충분한 검증이다.
        adapter = await self._resolver.get_adapter(self._user_id, self._exchange)
        await adapter.get_balance()
        return ScopeProof(
            granted_scopes=self._requested_capability_profile,
            provider_credential_ref=f"live:{self._exchange}:{self._user_id}",
            provider_verified=False,
        )

    async def fetch_snapshot(self, account_ref: OpaqueRef, as_of: datetime) -> ProviderSnapshot:
        adapter = await self._resolver.get_adapter(self._user_id, self._exchange)
        balances = await adapter.get_balance()
        positions = await adapter.get_positions()
        values = tuple(
            SnapshotValue(entity_type="BALANCE", entity_key=b.asset, value=b.total)
            for b in balances
        ) + tuple(
            SnapshotValue(entity_type="POSITION", entity_key=p.symbol, value=p.quantity)
            for p in positions
        )
        currency = balances[0].asset[:_CURRENCY_FIELD_MAX_LEN] if balances else _NO_BALANCE_CURRENCY
        return ProviderSnapshot(
            provider_as_of=datetime.now(timezone.utc),
            currency=currency,
            raw_payload_ref=f"live:{self._exchange}:{account_ref.value}:{as_of.isoformat()}",
            values=values,
        )
