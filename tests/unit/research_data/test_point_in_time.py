"""RD-20 — `domain/corporate_action/point_in_time.py` 순수 규칙 테스트.

Spec: docs/specs/L4_research_data_and_market_ecosystem_v1.0.md §9 RD-20.
DoD: 정정 공시 적용 후에도 정정 전 시점 질의가 정정 전 값을 돌려준다
(known_at/effective_at 분리, UPDATE 금지·새 행이 전제).
"""
from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal
from uuid import uuid4

import pytest

from src.foundation.market_data.contracts.v1 import CorporateAction
from src.foundation.market_data.domain.corporate_action.point_in_time import resolve_as_of

_EX_DATE = date(2026, 4, 1)


def _action(*, instrument_id, ratio: str, known_at: datetime, source_ref: str) -> CorporateAction:
    return CorporateAction(
        action_type="SPLIT",
        instrument_id=instrument_id,
        ex_date=_EX_DATE,
        ratio=Decimal(ratio),
        source_ref=source_ref,
        known_at=known_at,
    )


def test_correction_is_a_new_row_and_pre_correction_query_returns_pre_correction_value() -> None:
    instrument_id = uuid4()
    original = _action(
        instrument_id=instrument_id,
        ratio="10",
        known_at=datetime(2026, 3, 2, 9, 0, tzinfo=timezone.utc),
        source_ref="rcept-original",
    )
    correction = _action(
        instrument_id=instrument_id,
        ratio="5",  # 정정 공시: 실제로는 5:1 분할이었음
        known_at=datetime(2026, 3, 10, 9, 0, tzinfo=timezone.utc),
        source_ref="rcept-correction",
    )
    history = [original, correction]  # 둘 다 저장소에 남아 있다 — UPDATE가 아니다

    before_correction = resolve_as_of(history, datetime(2026, 3, 5, tzinfo=timezone.utc))
    after_correction = resolve_as_of(history, datetime(2026, 3, 15, tzinfo=timezone.utc))

    assert [a.ratio for a in before_correction] == [Decimal("10")]
    assert [a.source_ref for a in before_correction] == ["rcept-original"]
    assert [a.ratio for a in after_correction] == [Decimal("5")]
    assert [a.source_ref for a in after_correction] == ["rcept-correction"]


def test_as_of_before_known_at_returns_nothing() -> None:
    instrument_id = uuid4()
    action = _action(
        instrument_id=instrument_id,
        ratio="10",
        known_at=datetime(2026, 3, 2, 9, 0, tzinfo=timezone.utc),
        source_ref="rcept-original",
    )

    result = resolve_as_of([action], datetime(2026, 2, 1, tzinfo=timezone.utc))

    assert result == []


def test_action_without_known_at_is_excluded() -> None:
    instrument_id = uuid4()
    legacy_action = CorporateAction(
        action_type="SPLIT",
        instrument_id=instrument_id,
        ex_date=_EX_DATE,
        ratio=Decimal("2"),
        source_ref="legacy-la12-record",
    )  # known_at 없음 — 기존 LA-12 경로에서 온 레코드

    result = resolve_as_of([legacy_action], datetime(2026, 12, 1, tzinfo=timezone.utc))

    assert result == []


def test_naive_as_of_raises() -> None:
    action = _action(
        instrument_id=uuid4(),
        ratio="10",
        known_at=datetime(2026, 3, 2, 9, 0, tzinfo=timezone.utc),
        source_ref="rcept-original",
    )

    with pytest.raises(ValueError):
        resolve_as_of([action], datetime(2026, 3, 5))
