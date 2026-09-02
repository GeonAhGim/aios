"""LB-6 — 공급자 대사 규칙 조립(reconciliation_rules).

Spec: docs/specs/L4_market_data_positions_ledger_v1.0.md#§8.4, §9 LB-6,
FND-08 `src/foundation/reconciliation/**`.

내부 원장 값과 공급자(거래소) 응답을 FND-08 `EntitySnapshot`(공용 대사
계약)으로 조립한다. 실제 분류(HEALTHY/MINOR_DIFFERENCE/MATERIAL_MISMATCH
등)는 FND-08 `domain.rules.classify_item`의 책임이고, 이 리프는 그 입력을
만드는 것까지만 한다(단일 책임). 순수 함수만 — I/O·시계 직접 호출 금지.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal

from src.foundation.reconciliation.contracts.v1 import EntitySnapshot


@dataclass(frozen=True, slots=True)
class InternalEntityValue:
    """내부 원장에서 조회한 typed 값 하나. `entity_key`는
    `build_entity_snapshots` 입력 목록 안에서 유일해야 한다."""

    entity_type: str
    entity_key: str
    value: Decimal


def build_entity_snapshots(
    internal: Sequence[InternalEntityValue],
    provider: Mapping[str, Decimal | None],
) -> list[EntitySnapshot]:
    """내부 값 목록과 공급자 값 맵을 `entity_key`로 짝지어 `EntitySnapshot`
    목록을 만든다.

    `provider`에 `entity_key`가 아예 없으면(키 부재) `provider_value`는
    `None`이다 — 공급자가 그 항목을 응답에서 아예 빼먹은 것과 값이 0인
    것을 구분해야 한다(FND-08 §2 "0으로 해석하지 않는다"); `.get()`을 쓰고
    존재하지 않는 키에 기본값 0을 넣지 않는다.

    `internal`에 중복 `entity_key`가 있으면 호출자 버그다(대사 대상이
    모호해진다) — `ValueError`.
    """
    seen: set[str] = set()
    snapshots: list[EntitySnapshot] = []
    for item in internal:
        if item.entity_key in seen:
            raise ValueError(f"중복 entity_key: {item.entity_key!r}")
        seen.add(item.entity_key)
        snapshots.append(
            EntitySnapshot(
                entity_type=item.entity_type,
                entity_key=item.entity_key,
                internal_value=item.value,
                provider_value=provider.get(item.entity_key),
            )
        )
    return snapshots


def break_age(detected_at: datetime, now: datetime) -> timedelta:
    """브레이크가 감지된 시각부터 경과한 시간(§8.4 "발생 후 수 분 내
    표면화"의 근거 지표). 두 시각 모두 tz-aware여야 한다 — naive datetime은
    거래소/저장소 응답을 잘못 해석했다는 신호이지 정상 입력이 아니다."""
    if detected_at.tzinfo is None:
        raise ValueError("detected_at must be timezone-aware")
    if now.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    return now - detected_at
