"""FA-9 — 양시간축(valid_time·transaction_time) 순수 질의 규칙.

Spec: docs/specs/L4_ibor_fund_accounting_and_resilience_v1.0.md#FA-9
(§2.3 양시간축 장부, §3 계약 "양시간축", §4 FA-A2 UPDATE/DELETE 금지).

Postgres `TSTZRANGE`(`valid_from`·`valid_to`·`tx_from`·`tx_to`)에 대응하는
순수 구간 대수만 정의한다 — 실제 DDL·EXCLUDE 제약·UPDATE 금지 트리거는
FA-10의 몫이고, 이 모듈은 I/O를 전혀 하지 않는다.

경계 규약: 두 구간 모두 반열림 `[from, to)` — `from`은 포함, `to`는
배제(Postgres `TSTZRANGE`의 기본 표준형과 동일). "현재" 행은 `to=None`
으로 표현한다(무한대, `tx_to = 'infinity'`에 대응). tz-naive datetime은
입력 단계에서 거부한다(LB-1/EO-01 선례 —
`src/foundation/execution_ownership/domain/rules.py:is_lease_available`).

`as_of(valid_time, tx_time)`가 유일한 질의 커널이다. 명세가 말하는
"4종 질의"는 이 커널에 `valid_time`/`tx_time` 각각을 명시값 또는 `now`로
넣는 2×2 조합이다(아래 4개 함수 = 그 조합의 이름 있는 별칭):
  1. `current`               — (valid=now,  tx=now)  지금 아는 현재 사실
  2. `as_of_valid_time`      — (valid=given, tx=now)  최신 지식 기준 과거 시점
  3. `as_of_transaction_time`— (valid=now,   tx=given) 과거 지식 기준 "지금"
  4. `as_of_bitemporal`      — (valid=given, tx=given) 완전 소급(롤백) 질의
"""
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Generic, TypeVar

T = TypeVar("T")

FA_BITEMPORAL_OVERLAP = "FA_BITEMPORAL_OVERLAP"


class BitemporalOverlapError(ValueError):
    """FA_BITEMPORAL_OVERLAP(409) — 같은 키의 두 구간이 valid·tx 양쪽에서 겹친다.

    HTTP 매핑(§3 에러 taxonomy)은 API 계층 소관이다 — 이 모듈은 판정만 한다.
    """

    error_code = FA_BITEMPORAL_OVERLAP

    def __init__(self, first: BitemporalRecord[Any], second: BitemporalRecord[Any]):
        self.first = first
        self.second = second
        super().__init__(
            f"{FA_BITEMPORAL_OVERLAP}: valid/tx 구간이 겹친다 — "
            f"first=[{first.valid_from},{first.valid_to})x[{first.tx_from},{first.tx_to}) "
            f"second=[{second.valid_from},{second.valid_to})x[{second.tx_from},{second.tx_to})"
        )


def _require_tz_aware(value: datetime, *, name: str) -> None:
    if value.tzinfo is None:
        raise ValueError(f"{name}: naive datetime은 허용하지 않는다 — tz-aware UTC만 사용한다")


def _require_valid_bound(from_: datetime, to: datetime | None, *, label: str) -> None:
    _require_tz_aware(from_, name=f"{label}_from")
    if to is not None:
        _require_tz_aware(to, name=f"{label}_to")
        if to <= from_:
            raise ValueError(f"{label}_to는 {label}_from보다 뒤여야 한다(반열림 구간이 비어있음)")


@dataclass(frozen=True, slots=True)
class BitemporalRecord(Generic[T]):
    """상태성 테이블 한 행에 대응하는 순수 값 객체.

    `valid_from <= valid_time < valid_to`이고 `tx_from <= tx_time < tx_to`인
    좌표에서만 `value`가 유효하다. `valid_to`/`tx_to`가 `None`이면 무한대
    (각각 "아직 유효 종료 미정"·"아직 정정되지 않은 현재 행").
    """

    value: T
    valid_from: datetime
    valid_to: datetime | None
    tx_from: datetime
    tx_to: datetime | None

    def __post_init__(self) -> None:
        _require_valid_bound(self.valid_from, self.valid_to, label="valid")
        _require_valid_bound(self.tx_from, self.tx_to, label="tx")

    def _contains_valid(self, instant: datetime) -> bool:
        return self.valid_from <= instant and (self.valid_to is None or instant < self.valid_to)

    def _contains_tx(self, instant: datetime) -> bool:
        return self.tx_from <= instant and (self.tx_to is None or instant < self.tx_to)

    def contains(self, *, valid_time: datetime, tx_time: datetime) -> bool:
        """이 레코드가 주어진 (valid_time, tx_time) 좌표를 덮는가."""
        return self._contains_valid(valid_time) and self._contains_tx(tx_time)

    def overlaps(self, other: BitemporalRecord[Any]) -> bool:
        """valid·tx 두 구간이 모두 겹치면 True(FA_BITEMPORAL_OVERLAP 판정용)."""
        return _ranges_overlap(
            self.valid_from, self.valid_to, other.valid_from, other.valid_to
        ) and _ranges_overlap(self.tx_from, self.tx_to, other.tx_from, other.tx_to)


def _ranges_overlap(
    a_from: datetime, a_to: datetime | None, b_from: datetime, b_to: datetime | None
) -> bool:
    """반열림 구간 `[a_from, a_to)`와 `[b_from, b_to)`가 겹치는지(`None`=무한대)."""
    a_ends_before_b_starts = a_to is not None and a_to <= b_from
    b_ends_before_a_starts = b_to is not None and b_to <= a_from
    return not (a_ends_before_b_starts or b_ends_before_a_starts)


def check_no_overlap(records: Sequence[BitemporalRecord[T]]) -> None:
    """같은 키 그룹의 레코드 목록에서 valid·tx 구간이 겹치는 쌍이 있으면 거부한다.

    FA-A2(상태성 테이블 UPDATE/DELETE 금지 — 정정은 새 행 + `tx_to` 마감)를
    저장 전에 순수 함수 레벨로 사전 검증한다. 실제 DB 제약
    (`EXCLUDE USING gist`)은 FA-10의 몫이고, 여기서는 애플리케이션 레벨의
    fail-closed 방어선만 제공한다. 호출자는 이미 같은 논리 엔티티(예: 같은
    `position_id`)로 그룹핑한 레코드만 넘겨야 한다 — 이 함수는 그룹 경계를
    모른다.
    """
    for i, first in enumerate(records):
        for second in records[i + 1 :]:
            if first.overlaps(second):
                raise BitemporalOverlapError(first, second)


def as_of(
    records: Sequence[BitemporalRecord[T]], *, valid_time: datetime, tx_time: datetime
) -> list[BitemporalRecord[T]]:
    """질의 커널: `(valid_time, tx_time)` 좌표를 덮는 모든 레코드.

    정상적으로(겹침 없이) 적재된 단일 엔티티의 레코드라면 결과는 0개 또는
    1개다 — 겹침 방지는 `check_no_overlap`이 적재 시점에 보장한다. 이
    함수 자체는 그 불변조건을 가정하지 않고 그냥 필터링만 한다.
    """
    _require_tz_aware(valid_time, name="valid_time")
    _require_tz_aware(tx_time, name="tx_time")
    return [r for r in records if r.contains(valid_time=valid_time, tx_time=tx_time)]


def current(records: Sequence[BitemporalRecord[T]], *, now: datetime) -> list[BitemporalRecord[T]]:
    """질의 1/4: 지금(`now`) 시스템이 아는, 지금(`now`) 참인 사실."""
    return as_of(records, valid_time=now, tx_time=now)


def as_of_valid_time(
    records: Sequence[BitemporalRecord[T]], *, valid_time: datetime, now: datetime
) -> list[BitemporalRecord[T]]:
    """질의 2/4: 지금(`now`) 아는 최신 지식 기준으로, `valid_time`에 참이었던 사실.

    예: "오늘 알고 있는 바로, 지난달 15일 포지션은 얼마였나."
    """
    return as_of(records, valid_time=valid_time, tx_time=now)


def as_of_transaction_time(
    records: Sequence[BitemporalRecord[T]], *, tx_time: datetime, now: datetime
) -> list[BitemporalRecord[T]]:
    """질의 3/4: 과거 시스템 시각(`tx_time`)에, "지금"(`now`)에 대해 알던 사실.

    예: "어제 마감 시점(tx_time)에 시스템은 오늘자 포지션을 얼마로 알고
    있었나" — 정정 전 값을 재현하는 롤백 질의.
    """
    return as_of(records, valid_time=now, tx_time=tx_time)


def as_of_bitemporal(
    records: Sequence[BitemporalRecord[T]], *, valid_time: datetime, tx_time: datetime
) -> list[BitemporalRecord[T]]:
    """질의 4/4: 완전 소급 — 과거 시스템 시각(`tx_time`)에 시스템이 `valid_time`
    시점에 대해 참이라고 믿었던 사실. IBOR(지금 아는 진실)과 ABOR(그때 알던
    장부)를 같은 데이터로 재구성하는 §1 요구의 핵심 질의."""
    return as_of(records, valid_time=valid_time, tx_time=tx_time)
