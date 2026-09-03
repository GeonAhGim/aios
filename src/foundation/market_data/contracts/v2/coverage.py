"""DC-6 — 커버리지 선언(coverage span) 계약 v2.

Spec: docs/specs/L4_analytics_authoring_backtest_marketplace_v1.0.md
§2.1 DC-6, §4.1(fail-closed, `coverage_spans` 겹침 금지 EXCLUDE 제약),
§9.2 DC-6.

`domain/coverage/registry.py`(task-1127 decision)가 이 타입을 반환하고,
DC-7 `domain/coverage/gaps.py`의 `plan_fetch`가 그 반환 타입에 의존하므로
domain이 아니라 contracts에 둔다(DC-1 `instruments.py`와 동일 원칙 —
공개 계약은 순수 domain 모듈이 아니라 contracts가 SSOT).

커버리지 선언은 (벤처×자산군×TF×기간×품질등급) 축이다. `instrument_id`는
질의 키(§2.1 `coverage_for(instrument, tf)`)로 쓰기 위해 추가했다 —
DC-1 `Instrument`는 venue를 갖지 않으므로(venue는 `VenueListing` 소관)
이 계약이 venue·asset_class를 별도 필드로 갖는다.
"""
from __future__ import annotations

from enum import Enum
from typing import Literal

from pydantic import AwareDatetime, BaseModel, model_validator

from src.data.models.base import AssetClass
from src.foundation.market_data.contracts.v1 import Timeframe, Venue
from src.foundation.market_data.contracts.v2.instruments import ULID

SCHEMA_VERSION: Literal["coverage-v2"] = "coverage-v2"


class QualityGrade(str, Enum):
    """선언된 커버리지 구간의 신뢰 등급. 미검증: 거래소·벤더별 실제 등급
    구분 기준은 외부 계약서에 근거하지 않았다 — 이 3단계는 이 프로젝트가
    자체 정의한 내부 등급이다."""

    RAW = "RAW"
    VALIDATED = "VALIDATED"
    GOLD = "GOLD"


class CoverageSpan(BaseModel, frozen=True):
    """`[start_at, end_at)` 반개구간(half-open) — `end_at`은 배타적 상한.

    같은 (instrument_id, venue, asset_class, timeframe, quality_grade) 축
    안에서 두 span이 겹치는 것은 DB EXCLUDE 제약(§4.1)이 거부하는
    상태다 — `domain/coverage/registry.py`의 병합을 거치지 않은 원본
    선언 목록에는 일시적으로 존재할 수 있지만, 저장 직전에는 반드시
    병합돼야 한다.
    """

    instrument_id: ULID
    venue: Venue
    asset_class: AssetClass
    timeframe: Timeframe
    quality_grade: QualityGrade
    start_at: AwareDatetime
    end_at: AwareDatetime
    schema_version: Literal["coverage-v2"] = SCHEMA_VERSION

    @model_validator(mode="after")
    def _start_before_end(self) -> CoverageSpan:
        if self.end_at <= self.start_at:
            raise ValueError(
                f"CoverageSpan은 start_at < end_at이어야 한다: "
                f"start_at={self.start_at!r}, end_at={self.end_at!r}"
            )
        return self
