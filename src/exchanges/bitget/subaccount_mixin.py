"""02c_bitget_api_v2_extended_spec_v1.md §1.2 — BitgetAdapter Subaccount(서브계정) 메서드군.

Spec: 02c_bitget_api_v2_extended_spec_v1.md §1.2, §2(작업 분해 2번)

AIOS 자체 멀티테넌시(FD-11/12)와는 별개 개념 — 이건 **Bitget 계정
자체**의 서브계정 관리(예: 전략별로 거래소 계정을 분리하고 싶을 때).
`ExchangeAdapter` ABC에는 아직 없음(다른 확장 메서드들과 동일 원칙).
엔드포인트(커뮤니티 SDK 레퍼런스 기준, 라이브 검증 필요):
- GET  /api/v2/user/virtual-subaccount-list
- POST /api/v2/user/create-virtual-subaccount
- POST /api/v2/user/create-virtual-subaccount-apikey
- GET  /api/v2/user/virtual-subaccount-apikey-list
- GET  /api/v2/account/sub-account-assets
- POST /api/v2/spot/wallet/subaccount-transfer
"""
from __future__ import annotations

from decimal import Decimal
from typing import Any

from src.exchanges.common.live_guard import require_paper_sandbox

# 레드팀 #2026-09-02-34 — permissions를 생략하면 Bitget이 어떤 기본 권한을
# 부여하는지 코드/문서로 확인할 수 없었다(모듈 자신도 "라이브 검증
# 필요"라고 인정). 최소권한(조회 전용)을 클라이언트 쪽에서 명시적으로
# 강제해, 거래소의 미지정 시 기본 동작에 암묵적으로 의존하지 않는다.
_DEFAULT_SUBACCOUNT_PERMISSIONS = ["read"]


class BitgetSubaccountMixin:
    async def get_subaccounts(self, *, limit: int = 100) -> list[dict[str, Any]]:
        raw = await self._request(  # type: ignore[attr-defined]
            "GET", "/api/v2/user/virtual-subaccount-list", params={"limit": str(limit)}
        )
        return list(raw["data"].get("subAccountList", raw["data"]))

    async def create_subaccount(self, subaccount_name: str) -> dict[str, Any]:
        raw = await self._request(  # type: ignore[attr-defined]
            "POST",
            "/api/v2/user/create-virtual-subaccount",
            body={"subAccountName": subaccount_name},
        )
        return dict(raw["data"])

    @require_paper_sandbox
    async def create_subaccount_apikey(
        self,
        subaccount_uid: str,
        passphrase: str,
        *,
        permissions: list[str] | None = None,
    ) -> dict[str, Any]:
        """레드팀 #2026-09-02-34 — `permissions`를 생략해도 Bitget의
        미지정 시 기본값에 맡기지 않고 명시적으로 최소권한(read)을
        보낸다. 실제 필요한 권한을 아는 호출부는 그대로 지정하면 된다."""
        resolved_permissions = permissions if permissions is not None else (
            _DEFAULT_SUBACCOUNT_PERMISSIONS
        )
        body: dict[str, Any] = {
            "subAccountUid": subaccount_uid,
            "passphrase": passphrase,
            "permType": ",".join(resolved_permissions),
        }
        raw = await self._request(  # type: ignore[attr-defined]
            "POST", "/api/v2/user/create-virtual-subaccount-apikey", body=body
        )
        return dict(raw["data"])

    async def get_subaccount_apikeys(self, subaccount_uid: str) -> list[dict[str, Any]]:
        raw = await self._request(  # type: ignore[attr-defined]
            "GET",
            "/api/v2/user/virtual-subaccount-apikey-list",
            params={"subAccountUid": subaccount_uid},
        )
        return list(raw["data"])

    async def get_subaccount_assets(self, subaccount_uid: str) -> list[dict[str, Any]]:
        raw = await self._request(  # type: ignore[attr-defined]
            "GET", "/api/v2/account/sub-account-assets", params={"subUid": subaccount_uid}
        )
        return list(raw["data"])

    @require_paper_sandbox
    async def transfer_to_subaccount(
        self,
        subaccount_uid: str,
        coin: str,
        amount: Decimal,
        *,
        from_type: str = "spot",
        to_type: str = "spot",
    ) -> bool:
        """계정 내부(모회사↔서브계정) 이체 — 7.9 원칙 무관(외부 주소로
        나가는 출금이 아니다, `account_mixin.py::transfer()`와 동일 판단).
        레드팀 #2026-09-02-32/33 참조."""
        if amount <= 0:
            raise ValueError("amount는 0보다 커야 합니다.")
        raw = await self._request(  # type: ignore[attr-defined]
            "POST",
            "/api/v2/spot/wallet/subaccount-transfer",
            body={
                "subAccountUid": subaccount_uid,
                "coin": coin.upper(),
                "amount": str(amount),
                "fromType": from_type,
                "toType": to_type,
            },
        )
        return bool(raw.get("code") == "00000")
