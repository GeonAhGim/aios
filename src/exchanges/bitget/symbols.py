"""LA-19 — Bitget 심볼 변환을 `symbol_normalizer`(LA-7) 위임으로 교체.

Spec: docs/specs/L4_market_data_positions_ledger_v1.0.md#§9.2 LA-19.

FULL_AUDIT_2026-09-02.md §7 — 어댑터 자체 변환과 LA-7 단일 규칙(symbol_normalizer)이
따로 놀면 언젠가 서로 어긋난다(예: 조회 경로만 새 quote를 지원하도록 고치고
주문 경로는 깜빡하는 사고). 이 모듈은 이제 규칙을 직접 구현하지 않고
`symbol_normalizer`에 위임만 한다 — 8개 Bitget 믹스인이 이미 이 모듈의
`to_bitget_symbol`/`to_canonical_symbol`을 임포트하고 있어(모듈 상단 주석
이전 판 참조) 공개 시그니처(이름·인자·반환 타입)는 그대로 유지해 호출부를
건드리지 않는다(하위호환).
"""
from __future__ import annotations

from src.foundation.market_data.contracts.v1 import Venue
from src.foundation.market_data.domain.reference.symbol_normalizer import (
    to_canonical as _to_canonical,
)
from src.foundation.market_data.domain.reference.symbol_normalizer import (
    to_venue as _to_venue,
)

__all__ = ["to_bitget_symbol", "to_canonical_symbol"]


def to_bitget_symbol(canonical_symbol: str) -> str:
    """"BTC/USDT" -> "BTCUSDT" """
    return _to_venue(Venue.BITGET, canonical_symbol)


def to_canonical_symbol(bitget_symbol: str) -> str:
    """"BTCUSDT" -> "BTC/USDT" """
    return _to_canonical(Venue.BITGET, bitget_symbol)
