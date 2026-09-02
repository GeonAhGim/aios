"""LB-7 — 거래소 공급자 잔고 조회 포트.

Spec: docs/specs/L4_market_data_positions_ledger_v1.0.md#§2.3, §9 LB-7.

domain/application은 이 Protocol만 알고, 실제 구현(adapters/exchange_balance_source.py,
LB-16)은 모른다(71번 §4). 조회 실패는 예외로 전파해야 한다 — 빈 리스트로
대체하면 실제 잔고 0과 구분할 수 없다(FD-3.3, §2.3 adapters 행).
"""
from __future__ import annotations

from typing import Protocol, runtime_checkable
from uuid import UUID

from src.data.models.trading import AccountBalance


@runtime_checkable
class ProviderBalanceSource(Protocol):
    async def balances(self, connection_id: UUID) -> list[AccountBalance]:
        """`connection_id`(account_connection FK)에 연결된 거래소 계정의 잔고
        전체. 실패 시 예외를 던진다 — 빈 리스트를 반환해 "잔고 없음"으로
        위장하지 않는다."""
        ...
