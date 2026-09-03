"""DC-5 — 데이터 커버리지 구간(coverage span) 저장 포트.

Spec: docs/specs/L4_analytics_authoring_backtest_marketplace_v1.0.md
§2.1 DC-5·DC-6, §4.1(fail-closed), §9.2 DC-5.

`domain/coverage/registry.py`(DC-6, 아직 미구현)가 이 Protocol 위에 병합·질의
로직(순수)을 쌓는다 — 이 파일은 저장 포트만 정의하고 병합 규칙은 갖지
않는다. `CoverageSpan`은 DC-6이 아직 없어 이 포트가 저장 계약으로 직접
정의한다(§2.1 표 "벤처×자산군×TF×기간×품질등급"); DC-6은 이 타입을 가져다
쓴다. 겹침 금지는 DC-8 마이그레이션의 EXCLUDE 제약이 강제한다.
"""
from __future__ import annotations

from enum import Enum
from typing import Protocol, runtime_checkable

import asyncpg
from pydantic import AwareDatetime, BaseModel

from src.foundation.market_data.contracts.v1 import Timeframe, Venue


class CoverageQuality(str, Enum):
    PROVISIONAL = "PROVISIONAL"
    VALIDATED = "VALIDATED"


class CoverageSpan(BaseModel):
    instrument_id: str
    venue: Venue
    timeframe: Timeframe
    quality: CoverageQuality
    start: AwareDatetime
    end: AwareDatetime


@runtime_checkable
class CoverageRepository(Protocol):
    async def upsert_span(self, conn: asyncpg.Connection, span: CoverageSpan) -> CoverageSpan:
        """겹치는 구간 삽입은 어댑터가 DB EXCLUDE 제약 위반 예외를 던진다
        (§4.1) — 병합은 `domain/coverage/registry.py`(DC-6) 소관, 여기는
        저장만 한다."""
        ...

    async def list_spans(
        self, conn: asyncpg.Connection, instrument_id: str, timeframe: Timeframe
    ) -> list[CoverageSpan]:
        """`start` 오름차순. 선언된 span이 하나도 없으면 빈 리스트 — 이건
        "요청 구간을 0으로 채운다"(§4.1 금지)는 것과는 다른 얘기로, 그냥
        커버리지 선언 자체가 비어 있다는 사실 그대로다."""
        ...
