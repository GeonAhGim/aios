"""FA-11 — positions/domain/restatement.py: retroactive fill reflection.

Spec: docs/specs/L4_ibor_fund_accounting_and_resilience_v1.0.md#§9 FA-11
(선행 FA-10=task-2051).

`pos_journal`은 append-only(§4.3 "(position_key, sequence_no) 유일·연속") —
늦게 도착한 체결(`occurred_at`이 과거)도 새 `sequence_no`로만 꼬리에 붙을 수
있다. 그래서 이 모듈은 저널 순서를 재배열하지 않는다: 새 엔트리를 기존
fold 위에 그대로 접되(LB-5 `snapshot_builder.apply_one` 재사용, 재구현 금지),
그 결과를 FA-9 `core.bitemporal.BitemporalRecord` 두 벌로 감싼다 — 이전
"현재" 레코드는 `tx_to`를 닫고(append-only, UPDATE 없음), 새 레코드는
`valid_from`을 `now`가 아니라 `late_entry.occurred_at`으로 연다(그 사실은
원래부터 참이었고 지금 늦게 알았을 뿐이므로). 이 패턴은 FA-9 자체 테스트
(`tests/unit/core/test_bitemporal.py`의 RECORD_A/RECORD_B 정정 사례)와
동일하다.

기업행위(CORP_ACTION) 소급은 `snapshot_builder.apply_one`이 아직 그
`entry_type`을 모른다(LB-5 범위 밖, `UnsupportedEntryTypeError`) — 이 모듈은
`entry_type`을 가리지 않고 그대로 위임하므로, LB-5가 CORP_ACTION을 지원하는
순간 이 모듈도 별도 변경 없이 지원하게 된다.

순수 함수만 — I/O 없음, 시각은 `now` 인자로만 받는다.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime

from src.core.bitemporal import BitemporalRecord, check_no_overlap
from src.data.models.base import AssetClass
from src.foundation.positions.contracts.v1 import CostMethod, PositionJournalEntryView
from src.foundation.positions.domain.snapshot_builder import SnapshotFold, apply_one


class NotRetroactiveError(ValueError):
    """`late_entry.occurred_at`이 `prior`의 `valid_from`보다 이르지 않다 — 이건
    정정이 아니라 그냥 다음 정상 엔트리다(호출자가 판정해서 넘겨야 함)."""


class RecordAlreadyClosedError(ValueError):
    """`prior.tx_to`가 이미 닫혀 있다 — 정정은 현재(`tx_to=None`) 레코드
    위에만 적용한다. 정정을 다시 정정하려면 그 결과의 `restated_current`를
    새 `prior`로 다시 넘겨야 한다."""


def is_retroactive(late_entry: PositionJournalEntryView, current_valid_from: datetime) -> bool:
    """`late_entry`가 현재 유효 구간이 시작된 시점보다 이전에 일어난 사실인가."""
    return late_entry.occurred_at < current_valid_from


@dataclass(frozen=True, slots=True)
class Restatement:
    """정정 결과 = 닫힌 이전 레코드 + 새로 연 현재 레코드. 둘 다 append-only
    표현이다(FA-A2 — 어느 쪽도 in-place로 만들지 않는다)."""

    closed_prior: BitemporalRecord[SnapshotFold]
    restated_current: BitemporalRecord[SnapshotFold]


def restate_position(
    *,
    position_key: str,
    cost_method: CostMethod,
    asset_class: AssetClass,
    prior: BitemporalRecord[SnapshotFold],
    late_entry: PositionJournalEntryView,
    now: datetime,
) -> Restatement:
    """`late_entry`(다음 `sequence_no`로 이미 저널에 append된 엔트리)를
    `prior` fold 위에 접어 정정된 fold를 만들고, FA-9 정정 패턴(닫힌 이전 +
    새 현재)으로 감싼다."""
    if prior.tx_to is not None:
        raise RecordAlreadyClosedError(
            f"{position_key}: prior 레코드가 이미 tx_to={prior.tx_to}로 닫혀 있습니다."
        )
    if not is_retroactive(late_entry, prior.valid_from):
        raise NotRetroactiveError(
            f"{position_key}: late_entry.occurred_at={late_entry.occurred_at}가 "
            f"prior.valid_from={prior.valid_from}보다 이르지 않습니다 — 소급이 아닙니다."
        )

    restated_fold = apply_one(
        prior.value,
        late_entry,
        position_key=position_key,
        cost_method=cost_method,
        asset_class=asset_class,
    )

    closed_prior = replace(prior, tx_to=now)
    restated_current = BitemporalRecord(
        value=restated_fold,
        valid_from=late_entry.occurred_at,
        valid_to=None,
        tx_from=now,
        tx_to=None,
    )
    check_no_overlap([closed_prior, restated_current])
    return Restatement(closed_prior=closed_prior, restated_current=restated_current)
