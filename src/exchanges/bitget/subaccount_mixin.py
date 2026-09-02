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

    async def create_subaccount_apikey(
        self,
        subaccount_uid: str,
        passphrase: str,
        *,
        permissions: list[str] | None = None,
    ) -> dict[str, Any]:
        body: dict[str, Any] = {"subAccountUid": subaccount_uid, "passphrase": passphrase}
        if permissions is not None:
            body["permType"] = ",".join(permissions)
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
        나가는 출금이 아니다, `account_mixin.py::transfer()`와 동일 판단)."""
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
