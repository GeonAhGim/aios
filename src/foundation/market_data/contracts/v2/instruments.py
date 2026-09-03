"""DC-1 — 심볼 마스터(symbol master) 계약 v2.

Spec: docs/specs/L4_analytics_authoring_backtest_marketplace_v1.0.md
§2.1 DC-1, §3.2(심볼 마스터), §4.1(fail-closed 불변조건), §4.2(생애주기 전이표),
107_contract_versioning_and_compatibility_standard_v1.0.md §3.3(MAJOR 변경).

`contracts/v1.py`의 `InstrumentRef`는 canonical_symbol 문자열 키 기반 단일
레코드였다. 이 v2는 `instrument_id`(불변 ULID)와 `VenueListing`(벤처별 심볼
매핑, 이력 보존)을 분리한다 — 심볼 변경은 새 listing을 등록하고 구 listing의
`delisted_at`을 채우는 것이지 기존 레코드를 덮어쓰는 게 아니다(§3.2). 이는
필드 의미 변경(MAJOR)이므로 `v1.py`를 고치지 않고 `v2/`로 신설했다(107번 §3.3).

DC-2(symbol_master)·DC-3(lifecycle)·DC-5(ports)·DC-6(coverage registry)가 이
계약에 1:1 의존하므로, §3.2 표 밖의 필드를 임의로 추가하지 않는다.

모든 `datetime` 필드는 `AwareDatetime`으로 naive 값을 거부하고, 가격 관련
수치(`tick_size`, `lot_size`)는 `Decimal`(NUMERIC(30,10)과 동일 정밀도, float
금지)이다.
"""
from __future__ import annotations

import re
from decimal import Decimal
from enum import Enum
from typing import Annotated, Literal

from pydantic import AfterValidator, AwareDatetime, BaseModel

from src.data.models.base import AssetClass
from src.foundation.market_data.contracts.v1 import Venue

SCHEMA_VERSION: Literal["instruments-v2"] = "instruments-v2"

# Crockford Base32(대문자, I/L/O/U 제외), 26자, 첫 글자는 타임스탬프 오버플로
# 방지를 위해 0-7로 제한한다(ULID 스펙). 이 프로젝트는 `python-ulid` 등 외부
# 패키지에 의존하지 않고 문자열 포맷만 검증한다 — 발급은 DC-2(symbol_master)의
# 책임이고 이 계약은 형식만 강제한다.
_ULID_PATTERN = re.compile(r"^[0-7][0-9A-HJKMNP-TV-Z]{25}$")


def _validate_ulid(value: str) -> str:
    normalized = value.upper()
    if not _ULID_PATTERN.fullmatch(normalized):
        raise ValueError(f"invalid ULID: {value!r}")
    return normalized


ULID = Annotated[str, AfterValidator(_validate_ulid)]


class InstrumentLifecycle(str, Enum):
    """§4.2 심볼 생애주기 전이표의 상태. 전이 규칙 자체는 DC-3
    (`domain/instruments/lifecycle.py`)의 책임이며, 이 계약은 상태 값만
    정의한다."""

    PENDING = "PENDING"
    ACTIVE = "ACTIVE"
    HALTED = "HALTED"
    DELISTED = "DELISTED"


class Instrument(BaseModel):
    """벤처 독립적인 심볼 마스터 레코드. `instrument_id`는 불변(§4.1) —
    재상장은 같은 id를 재사용하지 않고 새 `Instrument`를 발급한다(§4.2
    delisted→relisted: "새 instrument 생성(구 id 유지 금지)")."""

    instrument_id: ULID
    asset_class: AssetClass
    base: str | None
    quote: str | None
    isin: str | None
    figi: str | None
    tick_size: Decimal
    lot_size: Decimal
    calendar_id: str
    lifecycle_state: InstrumentLifecycle
    created_at: AwareDatetime
    schema_version: Literal["instruments-v2"] = SCHEMA_VERSION


class VenueListing(BaseModel):
    """벤처별 심볼 매핑. `(venue, venue_symbol, listed_at)`는 유일해야
    한다(§3.2) — 이 유일성은 DC-4 마이그레이션의 DB 제약(EXCLUDE)이 실제로
    강제하고, 이 DTO는 그 계약 형태만 표현한다. 심볼 변경은 구 listing에
    `delisted_at`을 채우고 새 listing을 등록하는 방식으로 표현한다
    (`instrument_id`는 그대로)."""

    instrument_id: ULID
    venue: Venue
    venue_symbol: str
    listed_at: AwareDatetime
    delisted_at: AwareDatetime | None
    is_primary: bool
    schema_version: Literal["instruments-v2"] = SCHEMA_VERSION
