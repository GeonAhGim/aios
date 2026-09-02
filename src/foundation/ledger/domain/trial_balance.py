"""LC-5 — 시산표(trial balance): 합계 보존 증명.

Spec: docs/specs/L4_market_data_positions_ledger_v1.0.md#§3.3 `TrialBalanceView`,
§4.4 "시산표 Σ(차변 − 대변) = 0", §9 LC-5.

복식부기 항등식의 원장 버전: `posting_rules.lines_for`(LC-4)가 만드는 모든
분개는 개별적으로 Σ차변=Σ대변(`balance_rules.check_balanced`가 이미 강제)
이므로, 전체 분개를 계정별로 누적한 net(차변−대변)의 총합도 항상 0이어야
한다. 이 모듈은 그 누적·검증만 한다 — 개별 분개 균형 검증은 재구현하지
않고 LC-3/LC-4에 맡긴다. 순수 함수만: I/O·시계 직접 호출 금지, `as_of`는
인자로 받는다.
"""
from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from datetime import datetime
from decimal import Decimal

from src.foundation.ledger.contracts.v1 import PostingLine, Side, TrialBalanceView


class TrialBalanceNonZeroError(Exception):
    """`INTEGRITY_TRIAL_BALANCE_NONZERO` — Σ(차변−대변) != 0. fail-closed:
    원장 쓰기 전면 차단 대상(§3.3 에러 taxonomy). `balances`에 계정별 net을
    담아 어디가 어긋났는지 리포트한다."""

    def __init__(self, balances: Mapping[str, Decimal], total: Decimal) -> None:
        super().__init__(f"trial balance Σ={total} (!= 0), 계정 {len(balances)}개")
        self.balances = dict(balances)
        self.total = total


def apply_entry(
    balances: Mapping[str, Decimal], lines: Sequence[PostingLine]
) -> dict[str, Decimal]:
    """분개 한 건의 행들을 계정별 net(차변−대변)에 누적한 새 딕셔너리를
    반환한다(입력 `balances`는 변경하지 않음)."""
    updated = dict(balances)
    for line in lines:
        delta = line.amount if line.side is Side.DEBIT else -line.amount
        updated[line.account_code] = updated.get(line.account_code, Decimal("0")) + delta
    return updated


def build_trial_balance(entries: Iterable[Sequence[PostingLine]]) -> dict[str, Decimal]:
    """여러 분개(entry)를 계정별 net으로 접는다(fold). 순서 무관 — 덧셈은
    교환·결합 법칙을 따른다."""
    balances: dict[str, Decimal] = {}
    for lines in entries:
        balances = apply_entry(balances, lines)
    return balances


def total(balances: Mapping[str, Decimal]) -> Decimal:
    """Σ(차변−대변). 항등식이 성립하면 항상 0."""
    return sum(balances.values(), Decimal("0"))


def verify_zero_sum(balances: Mapping[str, Decimal]) -> None:
    """`total(balances) != 0`이면 `TrialBalanceNonZeroError`(불일치 리포트 포함)."""
    grand_total = total(balances)
    if grand_total != 0:
        raise TrialBalanceNonZeroError(balances, grand_total)


def to_view(
    balances: Mapping[str, Decimal], *, as_of: datetime, last_entry_seq: int
) -> TrialBalanceView:
    """검증 통과 후 `TrialBalanceView`로 스냅샷한다. 불일치면 뷰를 만들지
    않고 예외를 던진다(fail-closed — 깨진 시산표를 조회 가능하게 노출하지 않음)."""
    verify_zero_sum(balances)
    return TrialBalanceView(
        as_of=as_of,
        last_entry_seq=last_entry_seq,
        balances=dict(balances),
        total=total(balances),
    )
