"""LB-19 positions 읽기 API 응답 스키마 — HTTP 세부만 여기 두고, 계약 자체는
`src/foundation/positions/contracts/v1.py`를 그대로 감싼다(106번 §2, LB-17
docstring의 "별도 뷰 모델 금지" 원칙). 요청 본문 스키마는 없다 — 쓰기
엔드포인트가 없다.

저널 커서는 불투명 문자열이지만 내용은 마지막 `sequence_no`다(§4.3 저널은
`(position_key, sequence_no)` 단조 증가라 이 값 하나로 재개 지점이 정해진다).
디코딩 실패는 도메인이 아니라 전송 계층 오류라 여기서 `InvalidCursorError`로
표현하고 전역 핸들러가 VALIDATION_INVALID_FIELD 봉투로 번역한다."""
from __future__ import annotations

from datetime import date
from uuid import UUID

from pydantic import BaseModel

from src.foundation.positions.contracts.v1 import (
    NAVSnapshot,
    PositionJournalEntryView,
    PositionSnapshotView,
)

__all__ = [
    "InvalidCursorError",
    "NavSeriesResponse",
    "PositionJournalResponse",
    "PositionListResponse",
    "decode_cursor",
    "encode_cursor",
]


class InvalidCursorError(ValueError):
    """`cursor` 쿼리 파라미터가 이 API가 발급한 형식이 아니다."""


def encode_cursor(sequence_no: int) -> str:
    return str(sequence_no)


def decode_cursor(raw: str | None) -> int:
    """없으면 처음부터(0). 음수·비정수는 거부한다."""
    if raw is None or raw == "":
        return 0
    try:
        value = int(raw)
    except ValueError as exc:
        raise InvalidCursorError(f"cursor 형식이 올바르지 않습니다: {raw!r}") from exc
    if value < 0:
        raise InvalidCursorError(f"cursor는 0 이상이어야 합니다: {raw!r}")
    return value


class PositionListResponse(BaseModel):
    items: list[PositionSnapshotView]


class PositionJournalResponse(BaseModel):
    position_key: str
    items: list[PositionJournalEntryView]


class NavSeriesResponse(BaseModel):
    """`missing_dates`는 범위 안에서 아직 NAV가 산출되지 않은 날 — 0으로
    채우지 않고 빠졌다는 사실을 그대로 드러낸다(FD-3.3 "never assume zero")."""

    account_id: UUID
    start_date: date
    end_date: date
    items: list[NAVSnapshot]
    missing_dates: list[date]
