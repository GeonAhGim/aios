"""FA-11 — correction 단위 테스트.

Spec: docs/specs/L4_ibor_fund_accounting_and_resilience_v1.0.md#§9 FA-11.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from unittest.mock import Mock
from uuid import uuid4

import pytest

from src.data.models.base import Currency
from src.foundation.ledger.contracts.v1 import PostingLine, Side
from src.foundation.ledger.domain import balance_rules, correction
from src.foundation.ledger.domain.correction import (
    BlankReasonError,
    EmptyEntryError,
    NoOpCorrectionError,
    build_correction,
    reversal_lines,
)
from tests.conftest import PerfBudget

_DEBIT_ACCOUNT = "PLATFORM:CASH_CLEARING"


def _lines(*, amount: Decimal, currency: Currency = Currency.KRW) -> list[PostingLine]:
    return [
        PostingLine(
            line_no=1,
            account_code=_DEBIT_ACCOUNT,
            side=Side.DEBIT,
            amount=amount,
            currency=currency,
        ),
        PostingLine(
            line_no=2,
            account_code=f"USER:{uuid4()}:AVAILABLE",
            side=Side.CREDIT,
            amount=amount,
            currency=currency,
        ),
    ]


def _net_delta(lines: list[PostingLine], account_code: str) -> Decimal:
    net = Decimal("0")
    for line in lines:
        if line.account_code == account_code:
            net += line.amount if line.side is Side.DEBIT else -line.amount
    return net


def test_reversal_lines_flips_every_side() -> None:
    original = _lines(amount=Decimal("100.00"))
    reversed_ = reversal_lines(original)

    assert [line.side for line in reversed_] == [Side.CREDIT, Side.DEBIT]
    assert [line.account_code for line in reversed_] == [line.account_code for line in original]
    assert [line.amount for line in reversed_] == [line.amount for line in original]


def test_reversal_lines_nets_to_zero_per_account() -> None:
    original = _lines(amount=Decimal("100.00"))
    combined = original + reversal_lines(original)

    for line in original:
        assert _net_delta(combined, line.account_code) == Decimal("0")


def test_reversal_lines_rejects_empty_input() -> None:
    with pytest.raises(EmptyEntryError):
        reversal_lines([])


def test_build_correction_bundles_reversal_and_repost() -> None:
    original_id = uuid4()
    original = _lines(amount=Decimal("100.00"))
    corrected = _lines(amount=Decimal("150.00"))

    result = build_correction(
        original_entry_id=original_id,
        original_lines=original,
        corrected_lines=corrected,
        reason="late fee waiver — audited by ops",
    )

    assert result.original_entry_id == original_id
    assert result.reason.startswith("late fee")
    assert [line.side for line in result.reversal] == [Side.CREDIT, Side.DEBIT]
    assert result.repost == tuple(corrected)


def test_build_correction_rejects_blank_reason() -> None:
    with pytest.raises(BlankReasonError):
        build_correction(
            original_entry_id=uuid4(),
            original_lines=_lines(amount=Decimal("100.00")),
            corrected_lines=_lines(amount=Decimal("150.00")),
            reason="   ",
        )


def test_build_correction_rejects_noop_when_digest_matches() -> None:
    original = _lines(amount=Decimal("100.00"))
    identical = [line.model_copy() for line in original]

    with pytest.raises(NoOpCorrectionError):
        build_correction(
            original_entry_id=uuid4(),
            original_lines=original,
            corrected_lines=identical,
            reason="attempted no-op",
        )


def test_build_correction_rejects_empty_corrected_lines() -> None:
    with pytest.raises(EmptyEntryError):
        build_correction(
            original_entry_id=uuid4(),
            original_lines=_lines(amount=Decimal("100.00")),
            corrected_lines=[],
            reason="oops",
        )


def test_build_correction_propagates_unbalanced_corrected_lines() -> None:
    unbalanced = [
        PostingLine(
            line_no=1,
            account_code=_DEBIT_ACCOUNT,
            side=Side.DEBIT,
            amount=Decimal("100.00"),
            currency=Currency.KRW,
        ),
        PostingLine(
            line_no=2,
            account_code=f"USER:{uuid4()}:AVAILABLE",
            side=Side.CREDIT,
            amount=Decimal("90.00"),
            currency=Currency.KRW,
        ),
    ]

    with pytest.raises(balance_rules.UnbalancedEntryError):
        build_correction(
            original_entry_id=uuid4(),
            original_lines=_lines(amount=Decimal("100.00")),
            corrected_lines=unbalanced,
            reason="bad repost",
        )


# ---- 실패 주입(D2): 하류 의존(check_balanced)이 예외를 던지면 삼키지 않고 전파한다 ----


def test_failure_injection_check_balanced_error_propagates_not_swallowed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """build_correction은 reversal_lines(내부에서 check_balanced 호출)에
    의존한다. 그 의존이 예상 밖 예외(LC-3 구현 버그 등)를 던지면 조용히
    삼키고 "정정 성공"으로 위장해선 안 된다 — fail-closed로 그대로
    전파해야 한다."""
    failure = RuntimeError("injected balance_rules.check_balanced failure")
    monkeypatch.setattr(correction.balance_rules, "check_balanced", Mock(side_effect=failure))

    with pytest.raises(RuntimeError, match="injected balance_rules.check_balanced failure"):
        build_correction(
            original_entry_id=uuid4(),
            original_lines=_lines(amount=Decimal("100.00")),
            corrected_lines=_lines(amount=Decimal("150.00")),
            reason="dependency failure injection",
        )


# ---- 게이트 적색 재현(D2): digest 기반 no-op 판정을 합계 비교로 약화하면 놓친다 ----


def test_gate_red_noop_check_catches_account_swap_that_naive_sum_check_misses() -> None:
    """게이트 적색 재현: 만약 `NoOpCorrectionError` 판정이 (지금처럼)
    `lines_digest` 전체 비교가 아니라 "차/대변 합계만 같으면 no-op"이라는
    순진한 비교로 약화된다면, 금액은 그대로 두고 수취 계좌만 바꿔치기한
    정정(오기재 계좌를 바로잡는 정당한 정정)을 "변경 없음"으로 오판해
    `NoOpCorrectionError`를 던지며 막아버린다. 이 테스트가 적색이 된다는
    것은 no-op 판정이 digest 전체 비교에서 합계만 보는 방식으로 후퇴했다는
    뜻이다."""
    original_id = uuid4()
    amount = Decimal("100.00")
    original = _lines(amount=amount)

    corrected_wrong_account_fixed = [
        original[0].model_copy(),
        original[1].model_copy(update={"account_code": f"USER:{uuid4()}:AVAILABLE"}),
    ]

    def _side_totals(lines: list[PostingLine]) -> tuple[Decimal, Decimal]:
        debit = sum((line.amount for line in lines if line.side is Side.DEBIT), Decimal("0"))
        credit = sum((line.amount for line in lines if line.side is Side.CREDIT), Decimal("0"))
        return debit, credit

    # 합계만 보면 원본과 정정안이 동일해 "no-op"으로 오판할 상황을 구성했다.
    assert _side_totals(original) == _side_totals(corrected_wrong_account_fixed)

    result = build_correction(
        original_entry_id=original_id,
        original_lines=original,
        corrected_lines=corrected_wrong_account_fixed,
        reason="오기재 수취 계좌 정정",
    )

    assert result.repost == tuple(corrected_wrong_account_fixed)


# ---- D3: 동시성 — 순수 함수가 공유 상태 없이 재진입 가능함을 증명 ----


def test_adversarial_concurrent_build_correction_calls_do_not_cross_contaminate() -> None:
    """D3 동시성 증빙: `build_correction`/`reversal_lines`는 모듈 수준 가변
    상태를 갖지 않는 순수 함수여야 한다 — 여러 스레드가 서로 다른 입력으로
    동시에 호출해도 각자의 결과가 섞이지 않아야 한다(한 스레드의 원본 금액이
    다른 스레드의 결과에 새어 들어가면 숨은 공유 상태가 있다는 뜻)."""

    def _build(amount: Decimal) -> Decimal:
        entry_id = uuid4()
        original = _lines(amount=amount)
        corrected = _lines(amount=amount + Decimal("1.00"))
        result = build_correction(
            original_entry_id=entry_id,
            original_lines=original,
            corrected_lines=corrected,
            reason=f"concurrent correction {amount}",
        )
        return result.repost[0].amount

    amounts = [Decimal(n) for n in range(1, 33)]
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(_build, amounts))

    assert results == [amount + Decimal("1.00") for amount in amounts]


# ---- 성능 단언(DEPTH 감사 task-2724 D1 판정 근거, 1702/1701 DEEPEN 선례와 동일 패턴) ----


@pytest.mark.perf
def test_build_correction_hot_path_performance(perf_budget: PerfBudget) -> None:
    # build_correction은 정정 요청마다 호출되는 순수 함수(reversal_lines +
    # check_balanced x2 + lines_digest 비교)다. 10,000회 호출이 1s 내로
    # 끝나야 한다 — I/O 없는 순수 계약의 실측 증명. task-7434: wall-clock
    # perf_counter() 대신 공용 perf_budget(process_time 기반)으로 측정한다.
    original_id = uuid4()
    original = _lines(amount=Decimal("100.00"))
    corrected = _lines(amount=Decimal("150.00"))
    iterations = 10_000

    def _run_once() -> None:
        for _ in range(iterations):
            build_correction(
                original_entry_id=original_id,
                original_lines=original,
                corrected_lines=corrected,
                reason="perf regression guard",
            )

    perf_budget.assert_within(
        _run_once, budget_ms=1000.0, label=f"{iterations} build_correction calls"
    )
