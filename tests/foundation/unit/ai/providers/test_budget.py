"""Unit tests for `src/foundation/ai/providers/domain/budget.py` -- task-2639
AI-5 DoD ("상한 초과 429"). D2 depth (ADR-2026-09-09-C): negative >=3,
failure injection 1, numeric performance assertion 1, gate-red reproduction
1."""

from __future__ import annotations

import time
import uuid
from datetime import date, timedelta
from decimal import Decimal

import pytest

from src.foundation.ai.providers.domain import budget as budget_module
from src.foundation.ai.providers.domain.budget import (
    BudgetExceededError,
    BudgetRuleError,
    TenantBudget,
    is_new_period,
    remaining,
    reserve,
    roll_period,
)

_TODAY = date(2026, 9, 17)


def _budget(
    *,
    daily_cap: Decimal = Decimal("10.00"),
    spent_today: Decimal = Decimal("0"),
    period_start: date = _TODAY,
) -> TenantBudget:
    return TenantBudget(
        tenant_id=uuid.uuid4(),
        token_id=uuid.uuid4(),
        daily_cap=daily_cap,
        spent_today=spent_today,
        period_start=period_start,
    )


# --- 정상 경로 ---


def test_reserve_within_cap_increments_spent_today() -> None:
    b = _budget(daily_cap=Decimal("10.00"), spent_today=Decimal("2.00"))
    updated = reserve(b, Decimal("3.00"))
    assert updated.spent_today == Decimal("5.00")
    assert b.spent_today == Decimal("2.00")  # 원본은 불변


def test_reserve_exactly_at_remaining_boundary_succeeds() -> None:
    b = _budget(daily_cap=Decimal("10.00"), spent_today=Decimal("9.00"))
    updated = reserve(b, Decimal("1.00"))
    assert remaining(updated) == Decimal("0")


def test_roll_period_resets_spend_and_advances_period() -> None:
    b = _budget(spent_today=Decimal("9.00"), period_start=_TODAY)
    rolled = roll_period(b, _TODAY + timedelta(days=1))
    assert rolled.spent_today == Decimal("0")
    assert rolled.period_start == _TODAY + timedelta(days=1)


def test_is_new_period_false_for_same_day() -> None:
    b = _budget(period_start=_TODAY)
    assert is_new_period(b, _TODAY) is False
    assert is_new_period(b, _TODAY + timedelta(days=1)) is True


# --- 부정 테스트: 상한 초과, 잘못된 생성, 음수 비용, 조기 롤오버 (>=3) ---


def test_reserve_rejects_cost_exceeding_remaining_cap_raises_budget_exceeded() -> None:
    """DoD 원문: "상한 초과 429" -- 이 순수 예외가 애플리케이션 계층에서
    429(AI_BUDGET_EXCEEDED)로 매핑되는 근거다."""
    b = _budget(daily_cap=Decimal("10.00"), spent_today=Decimal("9.00"))
    with pytest.raises(BudgetExceededError) as exc_info:
        reserve(b, Decimal("1.01"))
    assert exc_info.value.requested == Decimal("1.01")
    assert exc_info.value.remaining == Decimal("1.00")
    assert exc_info.value.cap == Decimal("10.00")


def test_tenant_budget_rejects_negative_daily_cap() -> None:
    with pytest.raises(BudgetRuleError):
        _budget(daily_cap=Decimal("-1"))


def test_tenant_budget_rejects_negative_spent_today() -> None:
    with pytest.raises(BudgetRuleError):
        _budget(spent_today=Decimal("-1"))


def test_reserve_rejects_negative_cost() -> None:
    b = _budget()
    with pytest.raises(BudgetRuleError):
        reserve(b, Decimal("-0.01"))


def test_roll_period_rejects_rolling_before_period_actually_ends() -> None:
    """조기 롤오버는 정당한 지출 이력을 조용히 지워버리므로 거부한다."""
    b = _budget(period_start=_TODAY)
    with pytest.raises(BudgetRuleError):
        roll_period(b, _TODAY)


def test_remaining_can_go_negative_after_cap_reduction_and_reserve_stays_closed() -> None:
    """daily_cap이 이미 쓴 금액 아래로 줄어든 뒤(예: 테넌트 강등)에도
    remaining()은 조용히 0으로 clamp하지 않고 음수를 그대로 드러내며,
    그 상태에서 reserve()는 어떤 양수 비용도 fail-closed 거부해야 한다."""
    b = _budget(daily_cap=Decimal("5.00"), spent_today=Decimal("8.00"))
    assert remaining(b) == Decimal("-3.00")
    with pytest.raises(BudgetExceededError):
        reserve(b, Decimal("0.01"))


# --- 실패 주입: 상류 역직렬화 손상이 조용히 통과하지 않고 fail-closed 거부 ---


def test_reserve_rejects_string_contaminated_spent_today_from_upstream_serialization() -> None:
    """실패 주입: 상류(예산 저장소 역직렬화)가 손상되어 `spent_today`가
    Decimal이 아니라 문자열로 섞여 들어오면(CLAUDE.md #3 "Monetary amounts
    are Decimal, never float" 규율이 지키려는 것과 같은 부류의 타입 붕괴),
    Decimal과 str의 산술/비교는 이미 TypeError를 내므로 잘못된 통과/거부를
    조용히 내리지 않고 fail-closed 거부해야 한다(dataclass는 frozen이라
    __post_init__ 이후 직접 대입은 object.__setattr__로만 가능 -- 역직렬화
    손상을 흉내낸다)."""
    b = _budget(daily_cap=Decimal("10.00"), spent_today=Decimal("2.00"))
    object.__setattr__(b, "spent_today", "2.00")  # 주입된 손상: str, Decimal 아님
    with pytest.raises(TypeError):
        reserve(b, Decimal("1.00"))


# --- 수치 성능 단언: reserve() 핫 패스(모든 provider 호출 전 예산 판단) ---
# §7 SLO "제안 생성 <=60s(공급자 제외)" 예산 아래, reserve() 자체는 그 경로의
# CPU 전용(순수 산술) 구간이므로 훨씬 좁은 자체 예산을 건다.

_RESERVE_BUDGET_MS = 1.0


def _reserve_latencies_ms(iterations: int = 200) -> list[float]:
    b = _budget(daily_cap=Decimal("1000000.00"), spent_today=Decimal("0"))
    samples: list[float] = []
    for _ in range(iterations):
        started = time.perf_counter()
        reserve(b, Decimal("1.00"))
        samples.append((time.perf_counter() - started) * 1000)
    samples.sort()
    return samples


def _p95(samples: list[float]) -> float:
    return samples[min(int(len(samples) * 0.95), len(samples) - 1)]


def test_reserve_p95_latency_within_self_declared_budget() -> None:
    samples = _reserve_latencies_ms()
    p95_ms = _p95(samples)
    print(f"[AI-5 budget] reserve p95={p95_ms:.4f}ms budget<{_RESERVE_BUDGET_MS:.1f}ms")
    assert p95_ms < _RESERVE_BUDGET_MS


def test_reserve_budget_gate_actually_fails_past_budget(monkeypatch: pytest.MonkeyPatch) -> None:
    """게이트 적색 재현: reserve()가 위임하는 `remaining()` 경로(호출당
    1회)에 예산을 실제로 넘기는 지연을 주입했을 때 위 성능 단언이 진짜로
    AssertionError를 내는지 확인한다 -- 이 테스트가 없으면 위 단언이 항상
    통과하는 tautology인지 아무도 검증하지 못한다."""
    original_remaining = budget_module.remaining

    def _stalled_remaining(b: TenantBudget) -> Decimal:
        time.sleep(_RESERVE_BUDGET_MS / 1000.0)
        return original_remaining(b)

    monkeypatch.setattr(budget_module, "remaining", _stalled_remaining)

    samples = _reserve_latencies_ms(iterations=5)
    p95_ms = _p95(samples)
    with pytest.raises(AssertionError):
        assert p95_ms < _RESERVE_BUDGET_MS
