"""RD-20 — OpenDART 공시를 `CorporateAction`으로 정규화하는 순수 규칙.

Spec: docs/specs/L4_research_data_and_market_ecosystem_v1.0.md §9 RD-20,
ADR-2026-09-06-H D3(국내 기업행위는 벤더가 아니라 전자공시 원본에서 뽑는다).

**미검증**: `OpenDartFiling`은 금감원 OpenDART Open API가 실제로 반환하는
JSON 필드명을 그대로 매핑한 것이 아니다 — 그 원문(예: 배당결정·액면분할
결정·합병결정 각 API의 실제 필드명)을 이 안정된 내부 표현으로 옮기는 변환은
`adapters/opendart/`에서 실 API 연동 시 별도로 검증해야 한다. 이 모듈은 그
변환 이후의 표현만 다루며, 종목 식별(`instrument_id`) 해석도 이미 끝난
입력을 받는다(심볼→instrument_id 조회는 I/O라 순수 함수 밖에서 한다).

정정 공시(`corrects_rcept_no`가 채워짐)는 원본과 같은 `(instrument_id,
action_type, ex_date)`를 갖되 `known_at`(접수 시각)이 다른 새
`CorporateAction`을 만든다 — 기존 행을 고치지 않는다. 저장(append-only,
UPDATE 금지)은 `adapters/opendart/postgres_filing_repository.py`가 보장하고,
이 함수는 그 저장에 넘길 값만 순수하게 계산한다.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Literal
from uuid import UUID

from src.foundation.market_data.contracts.v1 import CorporateAction

__all__ = ["FilingParseError", "OpenDartFiling", "normalize_filing"]

_REPORT_TYPES = ("SPLIT", "CASH_DIVIDEND", "MERGER")


@dataclass(frozen=True, slots=True)
class OpenDartFiling:
    """OpenDART 공시 한 건에서 뽑아낸 최소 정규화 입력.

    `rcept_no`(DART 접수번호)는 공시 문서 하나를 식별하는 고유값이라
    `CorporateAction.source_ref`이자 저장소의 멱등키로 쓴다 — 정정 공시는
    원 공시와 다른 자신만의 `rcept_no`를 가지므로, 정정을 반영해도 원본
    행을 덮지 않고 새 `rcept_no` 행이 추가된다(그래서 UPDATE가 아니라 새
    행이 자연히 나온다).
    """

    instrument_id: UUID
    rcept_no: str
    report_type: Literal["SPLIT", "CASH_DIVIDEND", "MERGER"]
    event_date: date
    known_at: datetime
    corrects_rcept_no: str | None = None
    split_ratio_before: Decimal | None = None
    split_ratio_after: Decimal | None = None
    dividend_per_share: Decimal | None = None
    merger_ratio: Decimal | None = None


class FilingParseError(ValueError):
    """공시를 정규화할 수 없다 — 조용히 버리지 않고 호출자가 미처리 큐에
    남기도록 예외로 알린다(RD-20 DoD)."""

    def __init__(self, filing: OpenDartFiling, reason: str) -> None:
        super().__init__(
            f"rcept_no={filing.rcept_no} report_type={filing.report_type}: {reason}"
        )
        self.filing = filing
        self.reason = reason


def _require_positive(filing: OpenDartFiling, value: Decimal | None, field: str) -> Decimal:
    if value is None:
        raise FilingParseError(filing, f"{field} 없음")
    if value <= 0:
        raise FilingParseError(filing, f"{field}는 양수여야 함(받은 값: {value})")
    return value


def _split_ratio(filing: OpenDartFiling) -> Decimal:
    before = _require_positive(filing, filing.split_ratio_before, "split_ratio_before")
    after = _require_positive(filing, filing.split_ratio_after, "split_ratio_after")
    return before / after


def normalize_filing(filing: OpenDartFiling) -> CorporateAction:
    """공시 하나 -> `CorporateAction` 하나. 실패는 항상 `FilingParseError`."""
    if filing.known_at.tzinfo is None:
        raise FilingParseError(filing, "known_at는 tz-aware여야 함")
    if not filing.rcept_no:
        raise FilingParseError(filing, "rcept_no 없음")

    if filing.report_type == "SPLIT":
        ratio = _split_ratio(filing)
        cash_amount = None
    elif filing.report_type == "CASH_DIVIDEND":
        ratio = Decimal(1)
        cash_amount = _require_positive(filing, filing.dividend_per_share, "dividend_per_share")
    elif filing.report_type == "MERGER":
        ratio = _require_positive(filing, filing.merger_ratio, "merger_ratio")
        cash_amount = None
    else:  # pragma: no cover - Literal이 막지만 fail-closed 기본값
        raise FilingParseError(filing, f"알 수 없는 report_type: {filing.report_type}")

    return CorporateAction(
        action_type=filing.report_type,
        instrument_id=filing.instrument_id,
        ex_date=filing.event_date,
        ratio=ratio,
        cash_amount=cash_amount,
        source_ref=filing.rcept_no,
        known_at=filing.known_at,
    )
