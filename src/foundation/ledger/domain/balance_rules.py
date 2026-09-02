"""LC-3 — 분개 균형·잔액 불변조건.

Spec: docs/specs/L4_market_data_positions_ledger_v1.0.md#§3.3, §4.3, §4.4, §9 LC-3.

두 가지 불변을 강제한다:
1. `check_balanced` — 분개(entry) 하나의 행들은 통화가 하나여야 하고
   Σ차변 == Σ대변이어야 한다(§4.4 "시산표 Σ(차변−대변) = 0"의 엔트리 단위 판).
2. `apply` — 잔액에 델타를 적용한 뒤 `available = balance − held ≥ 0`
   (`allow_negative=True`인 계정, 즉 `USER:*:RECEIVABLE`만 예외, §4.4)과
   `held ≥ 0`을 강제한다(§9 LC-6 마이그레이션의 CHECK 제약과 동일 조건을
   DB에 닿기 전에 코드로 fail-closed).

`apply`는 `PostingLine`이 아니라 `delta_balance`/`delta_held`를 받는다 —
행 하나를 얼마만큼의 델타로 바꿀지는 계정 성격(§4.4 부호 규약,
`chart_of_accounts.account_type`)에 달려 있고 그 배선은 `posting_rules.py`
(LC-4)의 책임이다(`ports/balance_repository.py`의
`apply(conn, account_id, delta_balance, delta_held, expected_seq)`와 동일한
모양 — 이 함수는 그 호출 직전에 서는 순수 검증 계층이다). 순수 함수만 —
I/O·시계 직접 호출 금지.
"""
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, replace
from decimal import Decimal

from src.data.models.base import Currency
from src.foundation.ledger.contracts.v1 import PostingLine, Side


class UnbalancedEntryError(Exception):
    """`LEDGER_UNBALANCED_ENTRY` — Σ차변 != Σ대변. 규칙 버그, 재시도 불가."""


class CurrencyMismatchError(Exception):
    """`LEDGER_CURRENCY_MISMATCH` — 한 분개 안에 통화가 둘 이상. 호출자 버그."""


class InsufficientAvailableError(Exception):
    """`LEDGER_INSUFFICIENT_AVAILABLE` — 적용 후 `balance − held < 0`이고
    이 계정은 음수를 허용하지 않는다. 재시도 불가(잔액 변동 전이라 402로
    노출)."""


def check_balanced(lines: Sequence[PostingLine]) -> None:
    """엔트리 하나의 행들이 단일 통화·Σ차변=Σ대변인지 검사한다."""
    if not lines:
        raise UnbalancedEntryError("빈 분개는 균형을 판정할 수 없습니다.")

    currencies = {line.currency for line in lines}
    if len(currencies) > 1:
        codes = sorted(c.value for c in currencies)
        raise CurrencyMismatchError(f"한 분개에 통화가 섞였습니다: {codes}")

    debit_total = sum((line.amount for line in lines if line.side is Side.DEBIT), Decimal("0"))
    credit_total = sum((line.amount for line in lines if line.side is Side.CREDIT), Decimal("0"))
    if debit_total != credit_total:
        raise UnbalancedEntryError(
            f"차변 합({debit_total}) != 대변 합({credit_total})"
        )


@dataclass(frozen=True, slots=True)
class Balance:
    """`ledger_balance` 행 하나의 값 객체 스냅샷."""

    account_code: str
    balance: Decimal
    held: Decimal
    currency: Currency
    allow_negative: bool
    last_entry_seq: int


def apply(
    bal: Balance,
    *,
    delta_balance: Decimal = Decimal("0"),
    delta_held: Decimal = Decimal("0"),
    entry_seq: int,
) -> Balance:
    """`bal`에 델타를 적용한 새 `Balance`를 반환한다. 적용 후
    `held < 0`이거나(`allow_negative`와 무관하게 항상 금지) `allow_negative`가
    거짓인데 `balance − held < 0`이면 거부한다."""
    new_balance = bal.balance + delta_balance
    new_held = bal.held + delta_held

    if new_held < 0:
        raise InsufficientAvailableError(
            f"{bal.account_code}: held={new_held} — 음수 held는 항상 금지됩니다."
        )
    if not bal.allow_negative and (new_balance - new_held) < 0:
        raise InsufficientAvailableError(
            f"{bal.account_code}: available={new_balance - new_held} — "
            "이 계정은 음수 가용잔액을 허용하지 않습니다(allow_negative=False)."
        )

    return replace(
        bal,
        balance=new_balance,
        held=new_held,
        last_entry_seq=entry_seq,
    )
