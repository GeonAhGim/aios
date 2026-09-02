"""02c_bitget_api_v2_extended_spec_v1.md §1.7 — BitgetAdapter Broker(브로커/리셀러) 메서드군.

Spec: 02c_bitget_api_v2_extended_spec_v1.md §1.7, §2(작업 분해 9번)

AIOS는 Bitget 리셀러가 아니지만, 사용자가 브로커 계정으로 가입했을
가능성을 배제하지 않는다(요청 범위가 "모든 기능") — 실제 브로커 자격이
없으면 거래소가 오류를 반환할 뿐이며, 그건 호출부가 아니라 계정 상태의
문제다(8.3 원칙: 권한 없음도 정상 응답 케이스). `subaccount_mixin.py`의
Subaccount와는 별개 네임스페이스(`/api/v2/broker/*`, 브로커 하위
서브계정 전용 API). `ExchangeAdapter` ABC에는 아직 없음. 엔드포인트
(커뮤니티 SDK 레퍼런스 기준, 라이브 검증 필요):
- GET  /api/v2/broker/info
- GET  /api/v2/broker/account/subaccount-list
- POST /api/v2/broker/account/create-subaccount
- POST /api/v2/broker/account/create-subaccount-apikey
- GET  /api/v2/broker/account/subaccount-assets
- POST /api/v2/broker/account/subaccount-transfer
- GET  /api/v2/broker/account/subaccount-deposit
  (수수료 환급 조회 계열 — 정확한 경로는 공식 문서 미확인)
"""
from __future__ import annotations

from decimal import Decimal
from typing import Any


class BitgetBrokerMixin:
    async def get_broker_info(self) -> dict[str, Any]:
        raw = await self._request("GET", "/api/v2/broker/info")  # type: ignore[attr-defined]
        return dict(raw["data"])

    async def get_broker_subaccounts(self, *, limit: int = 100) -> list[dict[str, Any]]:
        raw = await self._request(  # type: ignore[attr-defined]
            "GET", "/api/v2/broker/account/subaccount-list", params={"limit": str(limit)}
        )
        return list(raw["data"].get("subAccountList", raw["data"]))

    async def create_broker_subaccount(self, subaccount_name: str) -> dict[str, Any]:
        raw = await self._request(  # type: ignore[attr-defined]
            "POST",
            "/api/v2/broker/account/create-subaccount",
            body={"subAccountName": subaccount_name},
        )
        return dict(raw["data"])

    async def create_broker_subaccount_apikey(
        self, subaccount_uid: str, passphrase: str, *, permissions: list[str] | None = None
    ) -> dict[str, Any]:
        body: dict[str, Any] = {"subAccountUid": subaccount_uid, "passphrase": passphrase}
        if permissions is not None:
            body["permType"] = ",".join(permissions)
        raw = await self._request(  # type: ignore[attr-defined]
            "POST", "/api/v2/broker/account/create-subaccount-apikey", body=body
        )
        return dict(raw["data"])

    async def get_broker_subaccount_assets(self, subaccount_uid: str) -> list[dict[str, Any]]:
        raw = await self._request(  # type: ignore[attr-defined]
            "GET",
            "/api/v2/broker/account/subaccount-assets",
            params={"subAccountUid": subaccount_uid},
        )
        return list(raw["data"])

    async def transfer_broker_subaccount(
        self,
        subaccount_uid: str,
        coin: str,
        amount: Decimal,
        *,
        from_type: str = "spot",
        to_type: str = "spot",
    ) -> bool:
        """브로커 계정↔하위 서브계정 내부 이체 — 7.9 원칙 무관(외부 출금
        아님, subaccount_mixin.py::transfer_to_subaccount와 동일 판단)."""
        raw = await self._request(  # type: ignore[attr-defined]
            "POST",
            "/api/v2/broker/account/subaccount-transfer",
            body={
                "subAccountUid": subaccount_uid,
                "coin": coin.upper(),
                "amount": str(amount),
                "fromType": from_type,
                "toType": to_type,
            },
        )
        return bool(raw.get("code") == "00000")

    async def get_broker_rebate_records(self, *, limit: int = 100) -> list[dict[str, Any]]:
        """수수료 환급(리베이트) 조회 — 정확한 엔드포인트 경로는 공식
        문서로 라이브 검증 전까지 미확정(모듈 docstring 참조)."""
        raw = await self._request(  # type: ignore[attr-defined]
            "GET", "/api/v2/broker/account/subaccount-deposit", params={"limit": str(limit)}
        )
        return list(raw["data"])
