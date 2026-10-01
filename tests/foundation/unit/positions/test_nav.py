"""LB-6 — nav 단위테스트.

Spec: docs/specs/L4_market_data_positions_ledger_v1.0.md#§9 LB-6
("체인 등식", `unit/positions/test_nav.py`: "체인 성립/불성립, closing=cash+mv").
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from uuid import uuid4

import pytest

from src.data.models.base import Currency
from src.foundation.positions.contracts.v1 import NAVSnapshot
from src.foundation.positions.domain import nav
from tests.conftest import PerfBudget

_ACCOUNT = uuid4()
_DAY = date(2026, 9, 3)


def _inputs(**overrides: object) -> nav.NavInputs:
    fields: dict[str, object] = {
        "account_id": _ACCOUNT,
        "nav_date": _DAY,
        "base_currency": Currency.USDT,
        "opening_nav": Decimal("1000"),
        "cash": Decimal("400"),
        "position_mvs": [Decimal("300"), Decimal("250")],
        "realized": Decimal("30"),
        "unrealized_delta": Decimal("15"),
        "funding": Decimal("2"),
        "fees": Decimal("3"),
        "flows": Decimal("0"),
    }
    fields.update(overrides)
    return nav.NavInputs(**fields)  # type: ignore[arg-type]


def _snapshot(**overrides: object) -> NAVSnapshot:
    computed = nav.compute_daily_nav(_inputs())
    return computed.model_copy(update=overrides)


def test_compute_daily_nav_closing_is_cash_plus_sum_of_position_mvs() -> None:
    result = nav.compute_daily_nav(_inputs())

    assert result.positions_mv == Decimal("550")  # 300 + 250
    assert result.closing_nav == Decimal("950")  # 400 + 550


def test_compute_daily_nav_with_no_open_positions_closing_is_cash() -> None:
    result = nav.compute_daily_nav(_inputs(position_mvs=[]))

    assert result.positions_mv == Decimal("0")
    assert result.closing_nav == result.cash


def test_compute_daily_nav_source_hash_is_deterministic() -> None:
    first = nav.compute_daily_nav(_inputs())
    second = nav.compute_daily_nav(_inputs())

    assert first.source_hash == second.source_hash


def test_compute_daily_nav_source_hash_changes_with_inputs() -> None:
    first = nav.compute_daily_nav(_inputs())
    second = nav.compute_daily_nav(_inputs(cash=Decimal("401")))

    assert first.source_hash != second.source_hash


def test_verify_chain_holds_when_both_equations_balance() -> None:
    # opening(1000) + realized(30) + Δunrealized(15) + funding(2) - fees(3) + flows(0) = 1044
    prev = _snapshot(closing_nav=Decimal("1000"))
    cur = _snapshot(
        opening_nav=Decimal("1000"),
        realized=Decimal("30"),
        unrealized_delta=Decimal("15"),
        funding=Decimal("2"),
        fees=Decimal("3"),
        flows=Decimal("0"),
        closing_nav=Decimal("1044"),
    )

    nav.verify_chain(prev, cur)  # 예외 없음


def test_verify_chain_rejects_opening_not_equal_to_prev_closing() -> None:
    prev = _snapshot(closing_nav=Decimal("1000"))
    cur = _snapshot(opening_nav=Decimal("999"), closing_nav=Decimal("1043"))

    with pytest.raises(nav.NavChainBrokenError):
        nav.verify_chain(prev, cur)


def test_verify_chain_rejects_closing_not_matching_rollforward_equation() -> None:
    prev = _snapshot(closing_nav=Decimal("1000"))
    cur = _snapshot(
        opening_nav=Decimal("1000"),
        realized=Decimal("30"),
        unrealized_delta=Decimal("15"),
        funding=Decimal("2"),
        fees=Decimal("3"),
        flows=Decimal("0"),
        closing_nav=Decimal("1044.01"),  # off by 0.01
    )

    with pytest.raises(nav.NavChainBrokenError):
        nav.verify_chain(prev, cur)


def test_verify_chain_does_not_absorb_rounding_even_by_smallest_decimal_unit() -> None:
    """허용오차 0 — 소수점 최소단위 차이도 반올림으로 흡수하지 않는다."""
    prev = _snapshot(closing_nav=Decimal("1000"))
    cur = _snapshot(
        opening_nav=Decimal("1000"),
        realized=Decimal("0.0000000001"),
        unrealized_delta=Decimal("0"),
        funding=Decimal("0"),
        fees=Decimal("0"),
        flows=Decimal("0"),
        closing_nav=Decimal("1000"),  # 정확히는 1000.0000000001이어야 함
    )

    with pytest.raises(nav.NavChainBrokenError):
        nav.verify_chain(prev, cur)


def test_verify_chain_accepts_negative_flows_and_fees_correctly() -> None:
    prev = _snapshot(closing_nav=Decimal("1000"))
    cur = _snapshot(
        opening_nav=Decimal("1000"),
        realized=Decimal("-20"),
        unrealized_delta=Decimal("-5"),
        funding=Decimal("0"),
        fees=Decimal("1"),
        flows=Decimal("-50"),
        closing_nav=Decimal("924"),  # 1000 - 20 - 5 + 0 - 1 - 50
    )

    nav.verify_chain(prev, cur)  # 예외 없음


# ── DEEPEN: 실패주입 / 성능 / 게이트 적색 / 적대적 리플레이 ──────────────────


def test_compute_daily_nav_propagates_snapshot_construction_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """실패 주입: 의존 모델(`NAVSnapshot`) 생성이 예외를 던지면
    `compute_daily_nav`가 삼키지 않고 그대로 전파해야 한다(fail-closed) —
    잘못된 스냅샷이 조용히 기본값/부분결과로 대체되는 것을 방지한다."""

    def _boom(*_args: object, **_kwargs: object) -> NAVSnapshot:
        raise ValueError("boom: downstream NAVSnapshot validation failed")

    monkeypatch.setattr(nav, "NAVSnapshot", _boom)

    with pytest.raises(ValueError, match="boom"):
        nav.compute_daily_nav(_inputs())


@pytest.mark.perf
def test_verify_chain_batch_within_latency_budget(perf_budget: PerfBudget) -> None:
    """수치 성능 단언: 순수 Decimal 등식 비교뿐이므로 10,000회 반복이
    200ms 예산 안에 끝나야 한다(O(1) per call 유지 확인)."""
    prev = _snapshot(closing_nav=Decimal("1000"))
    cur = _snapshot(
        opening_nav=Decimal("1000"),
        realized=Decimal("30"),
        unrealized_delta=Decimal("15"),
        funding=Decimal("2"),
        fees=Decimal("3"),
        flows=Decimal("0"),
        closing_nav=Decimal("1044"),
    )
    n = 10_000

    def _run_once() -> None:
        for _ in range(n):
            nav.verify_chain(prev, cur)

    perf_budget.assert_within(_run_once, budget_ms=200.0, label=f"{n} verify_chain calls")


def test_gate_red_verify_chain_catches_rollforward_tamper_that_naive_balance_check_misses() -> None:
    """게이트 적색 재현: 잔고 등식(`closing == cash + Σmv`)만 보는 나이브한
    검증은 `compute_daily_nav`가 구성 시점에 그 등식을 항상 만족시키도록
    만들기 때문에 롤포워드 변조를 전혀 잡지 못한다 — 이 테스트가 적색이
    된다는 것은 `verify_chain`의 롤포워드 등식 검사가 삭제되거나
    무력화됐다는 뜻이다."""
    prev = _snapshot(closing_nav=Decimal("1000"))
    tampered = _snapshot(
        opening_nav=Decimal("1000"),
        realized=Decimal("30"),
        unrealized_delta=Decimal("15"),
        funding=Decimal("2"),
        fees=Decimal("3"),
        flows=Decimal("0"),
        cash=Decimal("4450"),
        closing_nav=Decimal("5000"),  # cash(4450) + positions_mv(550) = 5000 → balance eq holds
    )

    naive_balance_check_passes = tampered.closing_nav == tampered.cash + tampered.positions_mv
    assert naive_balance_check_passes is True  # 나이브한 검증이라면 여기서 통과해버린다

    with pytest.raises(nav.NavChainBrokenError):  # 실제 게이트는 롤포워드 변조를 잡아낸다
        nav.verify_chain(prev, tampered)


def test_adversarial_source_hash_detects_tampered_snapshot_replay() -> None:
    """적대적 리플레이 증빙(D3): 공격자가 `compute_daily_nav`를 거치지 않고
    계산된 `NAVSnapshot`의 필드를 직접 변조해 재주입하면, 저장된
    `source_hash`는 변조 이전 입력 그대로 남아 변조된 값과 불일치한다 — 이
    불일치가 LB-15의 "다른 source_hash → POS_NAV_CHAIN_BROKEN" 재기록 거부
    근거가 된다(§8)."""
    original = nav.compute_daily_nav(_inputs())
    tampered = original.model_copy(update={"closing_nav": original.closing_nav + Decimal("1000")})

    assert tampered.source_hash == original.source_hash  # 공격자는 해시 필드 자체는 바꾸지 않았다

    recomputed_hash = nav._source_hash(
        _inputs(), positions_mv=tampered.positions_mv, closing_nav=tampered.closing_nav
    )
    assert recomputed_hash != original.source_hash  # 변조된 값으로 재계산하면 불일치가 드러난다
