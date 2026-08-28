"""12.2/12.4 — 거래소별 ExchangeAdapter 생성 팩토리.

Spec: 02_exchange_adapter_v1.2.md#§2.1, 13_multi_tenancy_auth_v1.4.md#§13.3

거래소마다 필요한 인증 필드 개수가 달라(Bitget: api_passphrase 1개,
KIS: cano/acnt_prdt_cd 2개) exchange_credentials.extra_encrypted 하나로
흡수한다 — 이 팩토리가 각 Adapter 생성자가 요구하는 형태로 풀어서 넘긴다.
"""
from __future__ import annotations

from src.exchanges.bitget.adapter import BitgetAdapter
from src.exchanges.common.adapter import ExchangeAdapter
from src.exchanges.kis.adapter import KISAdapter

SUPPORTED_EXCHANGES = ("bitget", "kis")


class UnsupportedExchangeError(Exception):
    """알 수 없는 exchange 값 또는 extra에 필요한 필드가 누락된 경우."""


def build_adapter(
    exchange: str,
    api_key: str,
    api_secret: str,
    extra: dict[str, str] | None,
    *,
    demo_mode: bool = True,
) -> ExchangeAdapter:
    extra = extra or {}

    if exchange == "bitget":
        try:
            api_passphrase = extra["api_passphrase"]
        except KeyError as exc:
            raise UnsupportedExchangeError(
                "Bitget은 api_passphrase가 필요합니다."
            ) from exc
        return BitgetAdapter(api_key, api_secret, api_passphrase, demo_mode=demo_mode)

    if exchange == "kis":
        try:
            cano = extra["cano"]
            acnt_prdt_cd = extra["acnt_prdt_cd"]
        except KeyError as exc:
            raise UnsupportedExchangeError(
                "KIS는 cano/acnt_prdt_cd가 필요합니다."
            ) from exc
        return KISAdapter(api_key, api_secret, cano, acnt_prdt_cd, is_paper_trading=demo_mode)

    raise UnsupportedExchangeError(f"지원하지 않는 거래소입니다: {exchange}")
