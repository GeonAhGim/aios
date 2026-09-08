"""RD-2 — `known_at` point-in-time 판정(순수, FA-9 위임).

Spec: docs/specs/L4_research_data_and_market_ecosystem_v1.0.md §2.1 RD-2,
§3(계약), §4 RD-A1("`known_at > as_of`인 항목은 어떤 조회 경로로도 반환되지
않는다").

`known_at`은 FA-9(`src/core/bitemporal.py`)의 `tx_from`과 같은 개념이다
(그 사실을 시스템이 알게 된 시각) — 이 모듈은 시각 비교 규칙을 다시 짜지
않고 `BitemporalRecord`/`as_of`를 그대로 호출한다(재구현 금지, RD-2 DoD
(d)). `ResearchItem`에는 `valid_from`/`valid_to`가 없으므로(연구 항목은
"그 시점에 발행된 사실"이지 유효구간을 갖는 상태가 아니다) `valid`축은
`known_at`으로 채워 넣고 무한히 연다 — 실질적으로 FA-9 4종 질의 중
`tx_time` 하나만 쓰는 특수형이지만, 비교 커널 자체는 공유한다.

domain/**는 I/O 임포트 금지(L0-2) — 이 파일은 순수 함수만 담는다.
"""
from __future__ import annotations

from datetime import datetime

from src.core.bitemporal import BitemporalRecord, as_of
from src.foundation.research_data.contracts.v1 import (
    ResearchDataErrorCode,
    ResearchItem,
)

__all__ = ["PointInTimeViolationError", "assert_point_in_time"]


class PointInTimeViolationError(ValueError):
    """`RD_POINT_IN_TIME_VIOLATION`(409) — `item.known_at`이 `as_of`보다
    뒤(strict)라 이 조회 시점에서는 존재를 알 수 없던 항목이다(RD-A1).
    재시도가 아니라 `as_of`를 바꿔 재조회해야 한다. HTTP 매핑은 API 계층
    소관 — 이 모듈은 판정만 한다."""

    error_code = ResearchDataErrorCode.POINT_IN_TIME_VIOLATION

    def __init__(self, item: ResearchItem, as_of_time: datetime) -> None:
        self.item = item
        self.as_of_time = as_of_time
        super().__init__(
            f"{self.error_code.value}: item_id={item.item_id} "
            f"known_at={item.known_at} as_of={as_of_time}"
        )


def assert_point_in_time(item: ResearchItem, as_of_time: datetime) -> None:
    """`item.known_at <= as_of_time`이면 통과, 아니면 `PointInTimeViolationError`.

    경계는 FA-9 `as_of` 커널이 정한다: `known_at`을 `valid_from`이자
    `tx_from`으로 삼는(둘 다 동일 좌표라 사실상 tx축 단일 비교로 수렴하는)
    1행짜리 `BitemporalRecord`를 만들어, `(as_of_time, as_of_time)` 좌표가
    그 반열림 구간 `[known_at, ∞)` 안에 있는지로 판정한다. `known_at ==
    as_of_time`은 포함(`>=`), `known_at`이 1마이크로초라도 뒤면 배제
    (`>`) — 이 부등호 방향은 여기서 손으로 뒤집을 수 없다(FA-9가 정한다).
    """
    record: BitemporalRecord[ResearchItem] = BitemporalRecord(
        value=item,
        valid_from=item.known_at,
        valid_to=None,
        tx_from=item.known_at,
        tx_to=None,
    )
    matches = as_of([record], valid_time=as_of_time, tx_time=as_of_time)
    if not matches:
        raise PointInTimeViolationError(item, as_of_time)
