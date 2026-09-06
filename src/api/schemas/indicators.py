"""IND-12 — `GET /v1/indicators` 응답 스키마 + 커서 검증.

Spec: docs/specs/L4_analytics_authoring_backtest_marketplace_v1.0.md#§9.9 IND-12.

커서는 마지막 항목 이름 그대로다(`positions.py` LB-19가 `sequence_no`를
그대로 문자열화하는 것과 같은 "불투명 문자열" 관례 — 별도 인코딩 계층 없이
형식만 검증한다). 디코딩 실패는 도메인이 아니라 전송 계층 오류라 여기서
`InvalidIndicatorCursorError`로 표현하고 전역 핸들러(EXCEPTION_MAP)가
VALIDATION_INVALID_FIELD 봉투로 번역한다(LB-19 `InvalidCursorError`와 동일
패턴, raw HTTPException 금지).
"""
from __future__ import annotations

import re

from pydantic import BaseModel

from src.core.indicators.catalog.registry_tiers import CatalogEntry, Tier

__all__ = [
    "IndicatorListItemView",
    "IndicatorListView",
    "InvalidIndicatorCursorError",
    "decode_cursor",
]

# 지표/스크립트 이름에 허용하는 charset — TA-Lib 함수명(대문자 영숫자)과
# 향후 스크립트 지표 이름(영숫자·_.:-)을 모두 포괄한다.
_CURSOR_RE = re.compile(r"^[A-Za-z0-9_.:-]{1,128}$")


class InvalidIndicatorCursorError(ValueError):
    """`cursor` 쿼리 파라미터가 이 API가 발급한 형식이 아니다."""


def decode_cursor(raw: str | None) -> str | None:
    """없으면 처음부터(None). 허용 charset 밖이거나 너무 길면 거부한다."""
    if raw is None or raw == "":
        return None
    if not _CURSOR_RE.match(raw):
        raise InvalidIndicatorCursorError(f"cursor 형식이 올바르지 않습니다: {raw!r}")
    return raw


class IndicatorListItemView(BaseModel):
    name: str
    tier: Tier
    category: str
    version: str
    hash: str
    inputs: tuple[str, ...]
    outputs: tuple[str, ...]

    @classmethod
    def from_entry(cls, entry: CatalogEntry) -> IndicatorListItemView:
        return cls(
            name=entry.name,
            tier=entry.tier,
            category=entry.category,
            version=entry.version,
            hash=entry.entry_hash,
            inputs=entry.spec.inputs,
            outputs=entry.spec.outputs,
        )


class IndicatorListView(BaseModel):
    items: list[IndicatorListItemView]
