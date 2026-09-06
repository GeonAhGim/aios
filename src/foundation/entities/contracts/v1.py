"""FA-1 — 엔티티 계층(LegalEntity/Fund/Portfolio/SubAccount) 계약 v1.

Spec: docs/specs/L4_ibor_fund_accounting_and_resilience_v1.0.md#FA-1 (§2.1 표·§4 FA-A1).

`LegalEntity → Fund → Portfolio → SubAccount` 4단 계층의 유일한 공개 표면.
`domain/`(hierarchy.py, defaults.py)은 이 파일을 import하지만, 이 파일은
`domain/`을 import하지 않는다(71번 §4, LC-1/LB-1과 동일 원칙). 필드 추가는
minor(107번, 기본값 필수) — 제거·의미 변경은 `v2` 모듈 신설.

Decimal 금전 필드는 없다(순수 식별·계층 정보) — LC-1/LB-1/DC-1의 "Decimal은
문자열로 직렬화" 관례는 해당 사항이 없다. `closed_at`은 §2.1 표에는 없지만
§4 "폐쇄 규칙"(hierarchy.py) 강제에 반드시 필요한 상태이므로 positions
모듈의 `closed_at: datetime | None` 관례(legacy_positions_projection.py)를
그대로 따라 4개 타입 전부에 추가했다 — `None`이면 활성(open), 값이 있으면
그 시각에 폐쇄됐고 이후 새 하위 엔티티를 붙일 수 없다(FA_HIERARCHY_VIOLATION).
"""
from __future__ import annotations

from datetime import date
from enum import Enum
from typing import Literal
from uuid import UUID

from pydantic import AwareDatetime, BaseModel

from src.data.models.base import Currency

SCHEMA_VERSION: Literal["v1"] = "v1"


class EntityErrorCode(str, Enum):
    """§3 에러 taxonomy 중 이 리프(FA-1)가 정의하는 계층 관련 코드. 실제
    예외 클래스는 `domain/hierarchy.py`의 책임이며 이 계약은 코드값만
    정의한다(LB-1 `PositionErrorCode`와 동일 원칙)."""

    HIERARCHY_VIOLATION = "FA_HIERARCHY_VIOLATION"  # 400, 상위 없음/폐쇄된 상위/순환
    ALREADY_CLOSED = "FA_ALREADY_CLOSED"  # 409, 이미 폐쇄된 엔티티 재폐쇄 시도
    CLOSE_BLOCKED_BY_CHILD = "FA_CLOSE_BLOCKED_BY_CHILD"  # 409, 활성 하위가 남아 폐쇄 불가


class LegalEntity(BaseModel):
    """§2.1: 다법인 축의 최상위. `region_tag`는 FA-24(데이터 주권)가
    저장 위치 판정에 재사용할 원본 태그이며 이 리프에서는 값만 보관한다."""

    entity_id: UUID
    tenant_id: UUID
    name: str
    jurisdiction: str
    region_tag: str
    closed_at: AwareDatetime | None = None
    schema_version: Literal["v1"] = SCHEMA_VERSION


class Fund(BaseModel):
    """§2.1: `base_currency`는 이 펀드 하위 Portfolio/SubAccount 전체에
    상속된다(hierarchy.py `resolve_*_currency`) — Portfolio/SubAccount는
    자기 통화 필드를 갖지 않는다. `mandate_ref`는 `mandates` 바운디드
    컨텍스트의 mandate id다 — 펀드 생성 시점에 mandate가 아직 없을 수
    있어(초안 상태) Optional이다; `None`이면 risk_gate는 아직 어떤
    mandate도 이 펀드에 묶이지 않은 것으로 취급한다."""

    fund_id: UUID
    entity_id: UUID
    base_currency: Currency
    mandate_ref: UUID | None = None
    inception: date
    closed_at: AwareDatetime | None = None
    schema_version: Literal["v1"] = SCHEMA_VERSION


class Portfolio(BaseModel):
    """§2.1: `venue_account_ref`는 거래소/증권사 쪽 계좌 식별자 문자열이다
    (형식은 벤더마다 달라 검증하지 않는다 — LC-1의 AccountCode와 달리
    이 값은 외부 시스템이 발급한 불투명 문자열)."""

    portfolio_id: UUID
    fund_id: UUID
    venue_account_ref: str
    closed_at: AwareDatetime | None = None
    schema_version: Literal["v1"] = SCHEMA_VERSION


class SubAccount(BaseModel):
    """§2.1: 블록 배분(FA-7/8)의 배분 대상 단위. `owner_ref`는 이 하위
    계좌의 수익자(개인 사용자 또는 별도 고객)를 가리키는 id다 — auth
    subject id와 같은 UUID 공간을 쓰지만 이 계약은 그 스코프를 강제하지
    않는다(도메인 계층이 모르는 다른 바운디드 컨텍스트라 참조만 보관)."""

    sub_account_id: UUID
    portfolio_id: UUID
    owner_ref: UUID
    closed_at: AwareDatetime | None = None
    schema_version: Literal["v1"] = SCHEMA_VERSION
