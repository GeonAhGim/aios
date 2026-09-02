"""LA-9 — 원시 캔들 공급 포트.

Spec: docs/specs/L4_market_data_positions_ledger_v1.0.md#§2.2, §9.2 LA-9.

domain/application은 이 Protocol만 알고, 실제 구현
(adapters/bitget_ingest_source.py, adapters/kis_ingest_source.py)은
모른다(71번 §4). 반환은 품질 게이트를 아직 통과하지 않은 `CandleRecord`
목록이다("Raw"는 검증 이전 상태를 뜻할 뿐, 새 DTO가 아니다) — 정렬·중복
제거·판정은 `domain/quality/*`(LA-4~6)·`application/ingest_candles.py`
소관. 조회 실패는 예외로 전파한다 — 빈 리스트로 대체하면 "데이터
없음"과 구분할 수 없다(positions `ProviderBalanceSource`와 같은 원칙).
"""
from __future__ import annotations

from typing import Protocol, runtime_checkable

from pydantic import AwareDatetime

from src.foundation.market_data.contracts.v1 import CandleRecord, Timeframe, Venue


@runtime_checkable
class IngestSource(Protocol):
    async def fetch_candles(
        self,
        venue: Venue,
        raw_symbol: str,
        tf: Timeframe,
        start: AwareDatetime,
        end: AwareDatetime,
    ) -> list[CandleRecord]:
        """`[start, end)` 범위. 데이터가 없으면 빈 리스트(오류 아님) —
        공급 실패는 예외로 던진다."""
        ...
