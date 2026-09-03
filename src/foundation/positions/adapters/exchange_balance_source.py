"""LB-16 — 거래소 잔고 소스(adapters/exchange_balance_source.py).

Spec: docs/specs/L4_market_data_positions_ledger_v1.0.md#§2.3, §9.3 LB-16.

`ports/exchange_balance_source.py`(LB-7)의 `ProviderBalanceSource`를
`src/exchanges/common/adapter.py`의 `ExchangeAdapter.get_balance`로 구현한다.
`connection_id` → `ExchangeAdapter` 인스턴스 매핑(자격증명 해석, 실제 거래소별
어댑터 생성)은 이 리프가 만들지 않는다 — 그건 `src/services/credential_resolver.py`
쪽 책임이고 여기서는 이미 만들어진 어댑터 인스턴스를 주입받는다(FROZEN LIVE 경로
개방 금지, ADR-2026-08-29-E — 이 클래스는 조회만 하고 자격증명을 다루지 않는다).

`get_balance()`가 예외를 던지면(네트워크 오류·인증 실패 등) 그대로 전파한다 —
FD-3.3 "조회 실패는 예외로 전파, 빈 리스트로 대체 금지"(빈 리스트를 반환하면
"잔고 없음"과 조회 실패를 구분할 수 없다).
"""
from __future__ import annotations

from collections.abc import Mapping
from uuid import UUID

from src.data.models.trading import AccountBalance
from src.exchanges.common.adapter import ExchangeAdapter

__all__ = ["ExchangeBalanceSource", "UnknownConnectionError"]


class UnknownConnectionError(KeyError):
    """`connection_id`에 매핑된 어댑터가 없다 — 배선 오류(자격증명 미등록과는
    다른 단계, 그건 어댑터 생성 이전 책임)."""


class ExchangeBalanceSource:
    """`ProviderBalanceSource` 구현 — `connection_id`별 `ExchangeAdapter`
    인스턴스 맵을 그대로 감싼다."""

    def __init__(self, adapters: Mapping[UUID, ExchangeAdapter]) -> None:
        self._adapters = adapters

    async def balances(self, connection_id: UUID) -> list[AccountBalance]:
        adapter = self._adapters.get(connection_id)
        if adapter is None:
            raise UnknownConnectionError(connection_id)
        return await adapter.get_balance()
