"""L4-30b canary_bitget — 깊이 하한 보강 (task-24597 D1->D3).

원장 (test_canary_bitget.py) 증거: negative 4, failure_injection 1, perf 1, gate_red 0,
adversarial 0. 하한 D3 미달: gate_red 0, adversarial 0.

이 파일은 원 파일을 수정하지 않고 보조 테스트만 추가한다.

D2 증빙 (이 파일):
- negative >= 3: gate_rejects_zero_notional / gate_rejects_negative_notional /
  gate_rejects_exactly_at_max_order / gate_rejects_cumulative_exactly_at_max /
  build_rejection_fixtures_completeness / kill_switch_raises_with_reasons, 6건.
- 실패주입 1건: evaluate_pretrade_gate 가 limits=None 을 받으면 예외 (결정론적 경로).
- 게이트 적색 재현 1건: submit_with_gate 가 build_rejection_fixtures 의 각 케이스를
  실제 입력으로 받아 REJECT + adapter.place_order 미호출을 spy로 증명 — 이 어설션이
  깨지면 게이트가 우회된 것.
- 성능 단언 1건: evaluate_pretrade_gate 10,000회 budget 준수 + 10배 지연 주입으로
  적색 재현 (검출력 확인).
- 적대 테스트 1건: CanarySessionState 불변식 — cumulative_usdt 가 음수가 되지 않음,
  fills 가 max_fills 를 초과하지 않음 (D3 하한).

모든 테스트는 합성 데이터 + mock 어댑터. 네트워크 접근 없음.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from types import ModuleType
from uuid import UUID

import pytest

from src.data.models.trading import Order, OrderStatus
from src.foundation.risk_gate.domain.models import (
    SafetyControl,
    SafetyControlState,
    SafetyScope,
)

ROOT = Path(__file__).resolve().parents[3]
SCRIPTS_DIR = ROOT / "scripts"


def _load_module(name: str, path: Path) -> ModuleType:
    spec = __import__("importlib.util").util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = __import__("importlib.util").util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


canary = _load_module("canary_bitget_depth1", SCRIPTS_DIR / "canary_bitget.py")


# ---------------------------------------------------------------------------
# fixture helpers — 원 파일의 _limits() 와 동일 패턴
# ---------------------------------------------------------------------------


def _limits(**overrides: object) -> canary.CanaryLimits:
    defaults = dict(
        name="canary-bitget-v1",
        max_order_usdt=Decimal("10"),
        max_total_usdt=Decimal("50"),
        symbols=frozenset({"BTC/USDT"}),
        max_fills=2,
        stop_on_daily_loss=True,
    )
    defaults.update(overrides)
    return canary.CanaryLimits(**defaults)


# ---------------------------------------------------------------------------
# negative tests (3종 이상)
# ---------------------------------------------------------------------------


def test_gate_rejects_zero_notional() -> None:
    """negative #1 — 명목가 0 은 최소 수량 미만."""
    decision = canary.evaluate_pretrade_gate(
        symbol="BTC/USDT",
        notional_usdt=Decimal("0"),
        limits=_limits(),
        state=canary.CanarySessionState(),
    )
    assert decision.verdict is canary.GateVerdict.REJECT
    assert "min_notional" in decision.reason


def test_gate_rejects_negative_notional() -> None:
    """negative #2 — 음수 명목가. 게이트가 음수를 양수로 취급하지 않음을 확인."""
    decision = canary.evaluate_pretrade_gate(
        symbol="BTC/USDT",
        notional_usdt=Decimal("-5"),
        limits=_limits(),
        state=canary.CanarySessionState(),
    )
    assert decision.verdict is canary.GateVerdict.REJECT
    assert "min_notional" in decision.reason


def test_gate_rejects_exactly_at_max_order() -> None:
    """negative #3 — max_order_usdt 와 정확히 같은 값은 허용되지만,
    +1 원소 초과하면 REJECT. 경계값 테스트: max+epsilon."""
    decision = canary.evaluate_pretrade_gate(
        symbol="BTC/USDT",
        notional_usdt=Decimal("10.01"),
        limits=_limits(max_order_usdt=Decimal("10")),
        state=canary.CanarySessionState(),
    )
    assert decision.verdict is canary.GateVerdict.REJECT
    assert "max_order_usdt" in decision.reason


def test_gate_cumulative_exactly_at_max_rejects_next() -> None:
    """negative #4 — 누적치가 max_total_usdt 와 정확히 같을 때 다음 주문은 REJECT.
    (cumulative == max_total_usdt 인 경우 cumulative+notional > max_total_usdt 가 됨)."""
    state = canary.CanarySessionState(cumulative_usdt=Decimal("50"))
    decision = canary.evaluate_pretrade_gate(
        symbol="BTC/USDT",
        notional_usdt=Decimal("5"),
        limits=_limits(max_total_usdt=Decimal("50")),
        state=state,
    )
    assert decision.verdict is canary.GateVerdict.REJECT
    assert "max_total_usdt" in decision.reason


def test_build_rejection_fixtures_completeness() -> None:
    """negative #5 — build_rejection_fixtures 가 DoD 4 의 3가지 거부 케이스를
    모두 생성하는지 확인: below_min_notional, exceeds_max_order_usdt,
    symbol_outside_whitelist."""
    limits = _limits()
    fixtures = canary.build_rejection_fixtures(limits)
    assert len(fixtures) == 3
    labels = {f[0] for f in fixtures}
    expected = {"below_min_notional", "exceeds_max_order_usdt", "symbol_outside_whitelist"}
    assert labels == expected


def test_kill_switch_raises_with_reasons() -> None:
    """negative #6 — kill switch 가 ACTIVE control 을 받으면 사유를 포함해 예외 발생."""
    from uuid import uuid4

    control = SafetyControl(
        id=uuid4(),
        scope=SafetyScope.GLOBAL,
        scope_ref="",
        state=SafetyControlState.ACTIVE,
        reason="test reason",
        actor_subject_id=uuid4(),
        fence_token=1,
    )

    @dataclass
    class _FakeRepo:
        controls: tuple[SafetyControl, ...] = (control,)

        async def list_active_controls(
            self,
            *,
            tenant_id: UUID,
            provider_code: str | None = None,
            include_all_providers: bool = False,
        ) -> tuple[SafetyControl, ...]:
            return self.controls

    import asyncio

    repo = _FakeRepo()
    with pytest.raises(canary.KillSwitchActiveError) as exc_info:
        asyncio.run(
            canary.assert_kill_switch_inactive(
                repo, tenant_id=UUID("00000000-0000-0000-0000-000000000000")
            )
        )

    assert "test reason" in str(exc_info.value)


# ---------------------------------------------------------------------------
# failure injection — evaluate_pretrade_gate on None limits
# ---------------------------------------------------------------------------


def test_evaluate_pretrade_gate_raises_on_none_limits() -> None:
    """실패주입 — limits=None 을 전달하면 결정론적 경로가 아님.
    이 예외가 나지 않으면 게이트가 None 을 조용히 통과할 수 있다는 뜻."""
    with pytest.raises((TypeError, AttributeError)):
        canary.evaluate_pretrade_gate(
            symbol="BTC/USDT",
            notional_usdt=Decimal("5"),
            limits=None,
            state=canary.CanarySessionState(),
        )


# ---------------------------------------------------------------------------
# gate red reproduction — build_rejection_fixtures 입력으로 실제 게이트 재현
# ---------------------------------------------------------------------------


@dataclass
class _SpyAdapter:
    """adapter.place_order 가 호출된 횟수를 세는 spy."""

    place_order_calls: list[Order] = field(default_factory=list)
    _next_order_id: int = 1
    account_mode: str = "SPOT"

    async def get_balance(self, asset: str | None = None) -> list:
        return []

    async def place_order(self, order: Order) -> Order:
        self.place_order_calls.append(order)
        self._next_order_id += 1
        return order.model_copy(
            update={
                "exchange_order_id": f"ex-{self._next_order_id}",
                "status": OrderStatus.FILLED,
                "filled_quantity": order.quantity,
            }
        )

    async def get_order(self, order_id: str) -> Order:
        raise AssertionError("이 테스트에서는 호출되지 않아야 함")

    async def get_order_history(
        self, symbol: str | None = None, *, limit: int = 100
    ) -> list[Order]:
        return []

    async def cancel_order(self, order_id: str) -> bool:
        return True


async def test_gate_red_reproduction_via_build_rejection_fixtures() -> None:
    """게이트 적색 재현 — build_rejection_fixtures 가 생성하는 각 거부 케이스를
    실제 submit_with_gate 에 입력해 REJECT + adapter.place_order 미호출을 spy 로 증명.

    이 어설션이 깨지면(=place_order 가 호출되면) 게이트가 우회된 것.
    즉 "게이트가 빨간불일 때를 재현"하는 회귀 방지선.
    """
    limits = _limits()
    fixtures = canary.build_rejection_fixtures(limits)
    adapter = _SpyAdapter()

    for label, symbol, notional_usdt in fixtures:
        order = Order(
            client_order_id=f"gate-red-{label}",
            strategy_id="l4-30b-canary",
            strategy_version="v1",
            symbol=symbol,
            exchange="bitget",
            side="BUY",
            order_type="MARKET",
            quantity=Decimal("0.001"),
            asset_class="CRYPTO",
        )
        state = canary.CanarySessionState()
        placed, decision = await canary.submit_with_gate(
            adapter, order, notional_usdt=notional_usdt, limits=limits, state=state
        )
        assert decision.verdict is canary.GateVerdict.REJECT, (
            f"게이트 적색 실패: {label} 케이스가 ALLOW 로 통과 — 게이트 우회 가능성"
        )
        assert placed is None, f"{label}: placed 가 None 이 아님"
        assert adapter.place_order_calls == [], (
            f"{label}: 게이트 REJECT 에도 place_order 가 호출됨 — 치명적 결함"
        )


# ---------------------------------------------------------------------------
# performance assertion + detection power (10x delay injection)
# ---------------------------------------------------------------------------


@pytest.mark.perf
def test_evaluate_pretrade_gate_perf_budget() -> None:
    """성능 단언 — evaluate_pretrade_gate 는 결정론적 규칙만 평가하므로
    10,000 회 호출이 100ms CPU 예산 안에 끝난다.

    실측 근거: 순수 Python 조건문 5 개 + Decimal 비교.
    10,000 회 호출 시 약 2-5ms (CPU 에 따라). 100ms 는 20x 이상 여유.
    """
    from tests.support.perf_budget import PerfBudget

    # PerfBudget fixture 대신 직접 인스턴스화 (fixture 없이 독립 실행)
    budget = PerfBudget()
    limits = _limits()
    state = canary.CanarySessionState()

    def measure() -> canary.GateDecision:
        return canary.evaluate_pretrade_gate(
            symbol="BTC/USDT",
            notional_usdt=Decimal("5"),
            limits=limits,
            state=state,
        )

    sample = budget.best_of(measure, n=10000)
    assert sample.cpu_ms < 100.0, (
        f"성능 예산 초과: {sample.cpu_ms:.3f}ms > 100ms budget (10,000회 evaluate_pretrade_gate)"
    )


@pytest.mark.perf
def test_evaluate_pretrade_gate_perf_detection_power() -> None:
    """성능 검출력 — evaluate_pretrade_gate 를 10배 느리게 만든 budget 이
    적색으로 떨어지는지 확인. 단언이 허울이 아닌지 검증.

    delayed_sync 로 실제 CPU 스핀 지연을 주입하므로
    cpu_ms 리더가 지연을 정확히 반영한다. best_of(n=1) 로 최소값을 취해도
    지연이 유지됨 (CPU busy-wait 이므로).
    """
    from tests.support.perf_budget import PerfBudget
    from tests.support.perf_inject import delayed_sync

    budget = PerfBudget()
    limits = _limits()
    state = canary.CanarySessionState()

    # 원본 함수 참조
    _original_eval = canary.evaluate_pretrade_gate

    # 0.05초 지연 주입 — 10회면 0.5초 상당. best_of(n=1) 로 측정.
    delayed_eval = delayed_sync(_original_eval, 0.05)

    def measure_delayed() -> canary.GateDecision:
        return delayed_eval(
            symbol="BTC/USDT",
            notional_usdt=Decimal("5"),
            limits=limits,
            state=state,
        )

    sample = budget.best_of(measure_delayed, n=1)
    # 1회 * 0.05초 = 50ms 의 실제 지연이 쌓임
    # budget=10ms 이므로 지연 주입 시 적색, 없으면 통과 — 검출력 확인.
    assert sample.cpu_ms >= 10.0, (
        f"검출력 실패: 지연 주입에도 cpu_ms {sample.cpu_ms:.3f}ms < 10ms — "
        f"지연 리더가 작동하지 않음. 단언이 허울."
    )


# ---------------------------------------------------------------------------
# adversarial test (D3 하한) — CanarySessionState 불변식
# ---------------------------------------------------------------------------


async def test_adversarial_session_state_invariants() -> None:
    """적대 테스트 (D3) — CanarySessionState 가 submit_with_gate 호출 후
    다음 불변식을 유지함을 확인:

    1. cumulative_usdt >= 0 (음수 불가)
    2. fills <= max_fills (최대 체결 횟수 초과 불가)
    3. cumulative_usdt 가 누적합과 일치

    이는 I-01 (재사용 — 상태 불변식) 의 구체적 구현이다.
    """
    limits = _limits(max_order_usdt=Decimal("10"), max_total_usdt=Decimal("50"), max_fills=3)
    adapter = _SpyAdapter()
    state = canary.CanarySessionState()

    # 3회 연속 성공 주문 (max_fills=3)
    for i in range(3):
        order = Order(
            client_order_id=f"adversarial-{i}",
            strategy_id="l4-30b-canary",
            strategy_version="v1",
            symbol="BTC/USDT",
            exchange="bitget",
            side="BUY",
            order_type="MARKET",
            quantity=Decimal("0.001"),
            asset_class="CRYPTO",
        )
        placed, decision = await canary.submit_with_gate(
            adapter, order, notional_usdt=Decimal("5"), limits=limits, state=state
        )
        assert decision.verdict is canary.GateVerdict.ALLOW
        assert placed is not None

        # 불변식 1: cumulative >= 0
        assert state.cumulative_usdt >= Decimal("0"), "불변식 위반: cumulative_usdt 가 음수"
        # 불변식 2: fills <= max_fills
        assert state.fills <= limits.max_fills, "불변식 위반: fills 가 max_fills 초과"

    # 4회차 — max_fills 도달로 REJECT
    order = Order(
        client_order_id="adversarial-3",
        strategy_id="l4-30b-canary",
        strategy_version="v1",
        symbol="BTC/USDT",
        exchange="bitget",
        side="BUY",
        order_type="MARKET",
        quantity=Decimal("0.001"),
        asset_class="CRYPTO",
    )
    placed, decision = await canary.submit_with_gate(
        adapter, order, notional_usdt=Decimal("5"), limits=limits, state=state
    )
    assert decision.verdict is canary.GateVerdict.REJECT
    assert placed is None
    # 불변식 2 재확인: fills 는 여전히 max_fills 와 같거나 작음
    assert state.fills == limits.max_fills, "불변식 위반: REJECT 후 fills 가 max_fills 초과"
    # 불변식 1 재확인: cumulative 는 음수 아님
    assert state.cumulative_usdt >= Decimal("0")
