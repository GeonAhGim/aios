"""RD-2 — research_data 계약 v1.

Spec: docs/specs/L4_research_data_and_market_ecosystem_v1.0.md §2.1 RD-2,
§3(계약 요지), §4 RD-A1/RD-A3,
107_contract_versioning_and_compatibility_standard_v1.0.md.

`domain/`은 이 파일을 import하지만, 이 파일은 `domain/`을 import하지 않는다
(71번 §4, FND-03·LB-1·LC-1과 동일 원칙). 필드 추가는 minor(107번, 기본값
필수) — 제거·의미 변경은 `v2` 모듈 신설.

모든 `datetime` 필드는 `AwareDatetime`으로 naive 값을 거부한다. `known_at`
없는 `ResearchItem`은 만들 수 없다(§3 "known_at 없는 항목은 저장 거부") —
pydantic이 필수 필드로 강제하므로 조회·저장 경로 진입 전에
`pydantic.ValidationError`로 막힌다.
"""
from __future__ import annotations

import enum
from typing import Literal
from uuid import UUID

from pydantic import AwareDatetime, BaseModel

__all__ = [
    "SCHEMA_VERSION",
    "ResearchItemKind",
    "RedistributionPolicy",
    "ResearchDataErrorCode",
    "ResearchItem",
    "SourceMeta",
]

SCHEMA_VERSION: Literal["v1"] = "v1"

ResearchItemKind = Literal["filing", "news", "macro", "alt"]
RedistributionPolicy = Literal["store_full", "store_excerpt", "link_only"]


class ResearchDataErrorCode(str, enum.Enum):
    """§3 에러 taxonomy 그대로 — 새 코드 발명 금지, 이 enum이 단일 출처다.
    실제 예외 클래스는 각 규칙을 구현하는 domain 리프의 책임이다
    (RD-2 `known_at.PointInTimeViolationError`, RD-3 이후 redistribution·
    수집 어댑터의 source availability/rate limit)."""

    POINT_IN_TIME_VIOLATION = "RD_POINT_IN_TIME_VIOLATION"  # 409, as_of를 바꿔 재조회(재시도 아님)
    REDISTRIBUTION_DENIED = "RD_REDISTRIBUTION_DENIED"  # 403, 불가
    SOURCE_UNAVAILABLE = "RD_SOURCE_UNAVAILABLE"  # 503, 재시도 가능
    RATE_LIMITED = "RD_RATE_LIMITED"  # 429, retry_after 후 재시도


class ResearchItem(BaseModel):
    """개별 리서치 항목(공시·뉴스·거시·대안데이터 공통 포락선).

    `known_at`은 그 사실을 시스템이 알 수 있었던 시각 — 백테스트·전략은
    `known_at <= bar_ts`만 읽는다(§1 시점 정합, RD-A1). 점검은
    `domain/known_at.assert_point_in_time`이 한다(이 파일은 계약만 정의).
    `revision_of`가 채워지면 이 항목은 그 `item_id`의 정정판이다(RD-3의
    입력, 이 리프에서는 체인 규칙을 강제하지 않는다).
    """

    item_id: UUID
    source_id: str
    kind: ResearchItemKind
    published_at: AwareDatetime
    known_at: AwareDatetime
    instruments: tuple[str, ...]
    title: str
    body_ref: str | None
    url: str
    language: str
    hash: str
    revision_of: UUID | None
    schema_version: Literal["v1"] = SCHEMA_VERSION


class SourceMeta(BaseModel):
    """소스별 메타 — 라이선스·수집 정책 강제 근거(RD-A3, RD-3의 입력).

    `rate_limit`은 분당 허용 요청 수(토큰버킷 파라미터 선언값, 실제 제한은
    수집 어댑터 소관 — `market_data/ports/provider.py`의 `RateLimitSpec`과
    동일 원칙). `coverage`는 사람이 읽는 커버리지 설명(예: "2015-01-01~
    present, 지연 5분") — 구조화된 커버리지 판정은 DC-19~22의 몫이다.
    """

    source_id: str
    publisher: str
    redistribution: RedistributionPolicy
    license_ref: str
    rate_limit: int
    coverage: str
    schema_version: Literal["v1"] = SCHEMA_VERSION
