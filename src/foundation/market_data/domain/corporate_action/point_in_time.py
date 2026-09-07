"""RD-20 — `known_at` 기준 point-in-time 조회 (순수 함수, I/O 없음).

Spec: docs/specs/L4_research_data_and_market_ecosystem_v1.0.md §9 RD-20.

저장소(`adapters/opendart/postgres_filing_repository.py`)는 정정 공시를
UPDATE 없이 새 행으로만 쌓는다 — 같은 `(instrument_id, action_type,
ex_date)`에 `known_at`이 다른 여러 `CorporateAction`이 있을 수 있다. 이
함수는 그 전체 이력을 받아 `as_of` 시점에 "우리가 알고 있었을" 값 하나만
골라낸다: 그룹별로 `known_at <= as_of`인 것 중 가장 늦은 `known_at`을 쓴다.
정정 전 시점을 물으면 정정 전 값이, 정정 후 시점을 물으면 정정된 값이
나온다 — 둘 다 저장소에 그대로 남아 있으므로 가능하다(UPDATE였다면 정정
전 값은 영원히 사라졌을 것이다).
"""
from __future__ import annotations

from collections.abc import Sequence
from datetime import date, datetime
from uuid import UUID

from src.foundation.market_data.contracts.v1 import CorporateAction

__all__ = ["resolve_as_of"]


def resolve_as_of(actions: Sequence[CorporateAction], as_of: datetime) -> list[CorporateAction]:
    if as_of.tzinfo is None:
        raise ValueError("as_of는 tz-aware datetime만 받는다")

    by_key: dict[tuple[UUID, str, date], CorporateAction] = {}
    for action in actions:
        known_at = action.known_at
        if known_at is None or known_at > as_of:
            continue
        key = (action.instrument_id, action.action_type, action.ex_date)
        current = by_key.get(key)
        current_known_at = current.known_at if current is not None else None
        if current_known_at is None or known_at > current_known_at:
            by_key[key] = action

    return sorted(by_key.values(), key=lambda a: (str(a.instrument_id), a.ex_date))
