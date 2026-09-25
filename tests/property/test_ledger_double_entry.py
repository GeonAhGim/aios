"""H-8 property 테스트 — 원장 복식부기(double-entry) 불변식.

Spec: docs/design/ADR-2026-09-09-B(H-8) + docs/specs/L4_market_data_positions_ledger_v1.0.md#§3.3,
§4.4, §9 LC-3/LC-4/LC-5, `src/foundation/ledger/domain/{posting_rules,balance_rules,
trial_balance}.py`.

복식부기 항등식: `posting_rules.lines_for`(LC-4)가 사건 하나를 분개행으로
바꿀 때 Σ차변 == Σ대변이고(LC-3 `check_balanced`가 반환 직전 강제), 여러
분개를 계정별로 fold한 시산표(LC-5)의 총합도 항상 0이어야 한다 —
개별 분개가 균형이면 덧셈의 교환·결합 법칙에 의해 전체 합도 균형이다.
이 파일은 순수 도메인 함수만 사용한다(DB 없음).
"""

from __future__ import annotations

from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from hypothesis import assume, given
from hypothesis import strategies as st

from src.data.models.base import Currency
from src.foundation.ledger.contracts.v1 import (
    LedgerEvent,
    LedgerEventType,
    PostingLine,
    Side,
)
from src.foundation.ledger.domain import trial_balance
from src.foundation.ledger.domain.balance_rules import (
    CurrencyMismatchError,
    UnbalancedEntryError,
    check_balanced,
)
from src.foundation.ledger.domain.posting_rules import lines_for

_UUIDS = st.uuids()
# ledger_journal_entry/ledger_posting_line은 NUMERIC(20,2) KRW quantize(§3.3) —
# `amount`는 항상 > 0(PostingLine.amount Field(gt=0)).
_AMOUNT = st.decimals(
    min_value=Decimal("0.01"), max_value=Decimal("100000000.00"), places=2, allow_nan=False
)
_COMMISSION_RATE = st.decimals(
    min_value=Decimal("0.0000"), max_value=Decimal("1.0000"), places=4, allow_nan=False
)
_CURRENCY = st.sampled_from(list(Currency))


def _simple_event(
    event_type: LedgerEventType,
    *,
    amount: Decimal,
    currency: Currency,
    extra: dict[str, Decimal | str] | None = None,
    **parties: UUID,
) -> LedgerEvent:
    return LedgerEvent(
        event_type=event_type,
        event_ref=f"prop-test:{uuid4()}",
        tenant_id=None,
        actor_subject_id=None,
        trace_id=uuid4(),
        amount=amount,
        currency=currency,
        parties=parties,
        extra=extra or {},
    )


_SIMPLE_PAIR_EVENTS = (
    (LedgerEventType.TOPUP_CONFIRMED, ("user",)),
    (LedgerEventType.HOLD_PLACED, ("buyer",)),
    (LedgerEventType.HOLD_RELEASED, ("buyer",)),
    (LedgerEventType.PAYOUT_RELEASE, ("seller",)),
)


def _random_simple_event(
    draw_uuid, amount: Decimal, currency: Currency, choice_idx: int
) -> LedgerEvent:
    event_type, party_keys = _SIMPLE_PAIR_EVENTS[choice_idx % len(_SIMPLE_PAIR_EVENTS)]
    parties = {key: draw_uuid() for key in party_keys}
    return _simple_event(event_type, amount=amount, currency=currency, **parties)


@given(_AMOUNT, _CURRENCY, st.integers(min_value=0, max_value=3), _UUIDS)
def test_single_pair_event_lines_balance_to_zero_net(
    amount: Decimal, currency: Currency, choice_idx: int, party_id: UUID
) -> None:
    event = _random_simple_event(lambda: party_id, amount, currency, choice_idx)
    lines = lines_for(event)
    net = trial_balance.total(trial_balance.apply_entry({}, lines))
    assert net == Decimal("0")


@given(_AMOUNT, _CURRENCY, _COMMISSION_RATE, _UUIDS, _UUIDS)
def test_hold_captured_split_lines_balance_to_zero_net(
    amount: Decimal, currency: Currency, rate: Decimal, buyer_id: UUID, seller_id: UUID
) -> None:
    """HOLD_CAPTURED는 수수료·정산액으로 갈라지는 다중 행 분개다 — 반올림
    분할 후에도 Σ차변=Σ대변이 깨지지 않아야 한다(`rounding.split_commission`
    이 잔차 없이 정확히 나눈다는 전제를 원장 레벨에서 재확인)."""
    event = _simple_event(
        LedgerEventType.HOLD_CAPTURED,
        amount=amount,
        currency=currency,
        extra={"commission_rate": rate},
        buyer=buyer_id,
        seller=seller_id,
    )
    lines = lines_for(event)
    net = trial_balance.total(trial_balance.apply_entry({}, lines))
    assert net == Decimal("0")


@given(
    st.lists(
        st.tuples(_AMOUNT, _CURRENCY, st.integers(min_value=0, max_value=3), _UUIDS),
        min_size=1,
        max_size=20,
    )
)
def test_folding_many_independent_entries_keeps_the_grand_total_at_zero(
    rows: list[tuple[Decimal, Currency, int, UUID]],
) -> None:
    """복식부기 핵심 불변식 — 서로 무관한 분개를 임의의 개수·순서로
    계정별 net에 누적(fold)해도 전체 총합은 항상 0이다(§4.4)."""
    entries = [
        lines_for(_random_simple_event(lambda p=party_id: p, amount, currency, choice_idx))
        for amount, currency, choice_idx, party_id in rows
    ]
    folded = trial_balance.build_trial_balance(entries)
    trial_balance.verify_zero_sum(folded)  # 예외를 던지지 않는다는 것이 곧 단언.
    assert trial_balance.total(folded) == Decimal("0")


# ---- 음성 테스트: 불균형 분개는 거부돼야 한다 ----


def test_check_balanced_rejects_single_sided_entry() -> None:
    unbalanced = [
        PostingLine(
            line_no=1,
            account_code="PLATFORM:CASH_CLEARING",
            side=Side.DEBIT,
            amount=Decimal("10.00"),
            currency=Currency.KRW,
        )
    ]
    with pytest.raises(UnbalancedEntryError):
        check_balanced(unbalanced)


@given(_AMOUNT, _AMOUNT)
def test_check_balanced_rejects_mismatched_debit_credit_totals(
    debit_amount: Decimal, credit_amount: Decimal
) -> None:
    assume(debit_amount != credit_amount)
    lines = [
        PostingLine(
            line_no=1,
            account_code="PLATFORM:CASH_CLEARING",
            side=Side.DEBIT,
            amount=debit_amount,
            currency=Currency.KRW,
        ),
        PostingLine(
            line_no=2,
            account_code="USER:00000000-0000-0000-0000-000000000001:AVAILABLE",
            side=Side.CREDIT,
            amount=credit_amount,
            currency=Currency.KRW,
        ),
    ]
    with pytest.raises(UnbalancedEntryError):
        check_balanced(lines)


def test_check_balanced_rejects_mixed_currency_entry() -> None:
    lines = [
        PostingLine(
            line_no=1,
            account_code="PLATFORM:CASH_CLEARING",
            side=Side.DEBIT,
            amount=Decimal("10.00"),
            currency=Currency.KRW,
        ),
        PostingLine(
            line_no=2,
            account_code="USER:00000000-0000-0000-0000-000000000001:AVAILABLE",
            side=Side.CREDIT,
            amount=Decimal("10.00"),
            currency=Currency.USDT,
        ),
    ]
    with pytest.raises(CurrencyMismatchError):
        check_balanced(lines)


# ---- 실패 주입: 시산표 레벨에서 드리프트가 검출돼야 한다 ----


@given(_AMOUNT)
def test_trial_balance_detects_an_injected_one_sided_line(amount: Decimal) -> None:
    """실패 주입 — 정상 분개들을 fold한 뒤, 어느 계정에 한쪽 방향으로만
    금액을 몰래 주입하면(예: 버그로 상대 행이 누락) `verify_zero_sum`이
    반드시 `TrialBalanceNonZeroError`로 잡아내야 한다(LC-10 무결성 검증의
    핵심 전제)."""
    good_entry = lines_for(
        _simple_event(
            LedgerEventType.TOPUP_CONFIRMED,
            amount=amount,
            currency=Currency.KRW,
            user=uuid4(),
        )
    )
    folded = trial_balance.build_trial_balance([good_entry])
    trial_balance.verify_zero_sum(folded)  # 주입 전에는 정상.

    corrupted = dict(folded)
    corrupted["PLATFORM:COMMISSION_REVENUE"] = corrupted.get(
        "PLATFORM:COMMISSION_REVENUE", Decimal("0")
    ) + Decimal("0.01")
    with pytest.raises(trial_balance.TrialBalanceNonZeroError):
        trial_balance.verify_zero_sum(corrupted)
