"""scripts/canary_bitget.py 성능 테스트 -- RATCHET-2, task-3256.

evaluate_pretrade_gate CPU 예산 단언과 검출력(detection power) 검증.
주 테스트 파일(test_canary_bitget.py)과 동일한 canary 모듈을 로딩한다.
"""

from __future__ import annotations

import importlib.util
import sys
from decimal import Decimal
from pathlib import Path
from types import ModuleType

import pytest

from tests.conftest import PerfBudget

_ROOT = Path(__file__).resolve().parents[3]
_SCRIPTS_DIR = _ROOT / "scripts"


def _load_module(name: str, path: Path) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


canary = _load_module("canary_bitget", _SCRIPTS_DIR / "canary_bitget.py")


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


@pytest.mark.perf
def test_evaluate_pretrade_gate_throughput_budget(
    perf_budget: PerfBudget,
) -> None:
    """10,000회 호출이 1,000ms CPU 예산 안에 끝난다."""
    limits = _limits()
    state = canary.CanarySessionState()

    def measure() -> None:
        for _ in range(10_000):
            canary.evaluate_pretrade_gate(
                symbol="BTC/USDT", notional_usdt=Decimal("1"), limits=limits, state=state
            )

    perf_budget.assert_within(measure, budget_ms=1000.0, label="evaluate_pretrade_gate 10k 호출")


@pytest.mark.perf
def test_evaluate_pretrade_gate_detection_budget_violation(
    perf_budget: PerfBudget, monkeypatch: pytest.MonkeyPatch
) -> None:
    """검출력 확인: evaluate_pretrade_gate를 10배 느리게 만든 budgets.assert_within
    가 적색으로 떨어지는지 검증 — 단언이 허울이 아닌 실제 검출력을 갖는지 확인.

    evaluate_pretrade_gate 는 CPU 순수 함수이므로 CPU 부하를 주입한다.
    original 함수를 10배 더 많은 비교 연산을 수행하도록 래핑한 뒤,
    동일한 1,000ms 예산 단언이 깨지는 것을 확인한다.
    """
    limits = _limits()
    state = canary.CanarySessionState()

    # 원본 함수 참조
    _original = canary.evaluate_pretrade_gate

    def _slower_symbol_check(symbol: str, symbols: frozenset) -> bool:
        """원본 symbol in checks 에 10배 더 많은 비교를 더한다."""
        # 원본 검사와 동일하게 동작하되, 더미 원소 5,000개를 추가해
        # CPU 부하를 인위적으로 증가시킨다(10배 지연 목표).
        _dummy = set(f"DUMMY_{i}" for i in range(5_000))
        _dummy.update(symbols)
        result = symbol in _dummy
        # 원본 결과 보존
        assert result is (symbol in symbols)
        return result

    def _patched_eval(
        *,
        symbol: str,
        notional_usdt: Decimal,
        limits: canary.CanaryLimits,
        state: canary.CanarySessionState,
        min_notional_usdt: Decimal = Decimal("1"),
    ) -> canary.GateDecision:
        # symbol in whitelist 검사에 CPU 부하 주입
        _slower_symbol_check(symbol, limits.symbols)
        # 나머지 검사는 원본과 동일
        if notional_usdt < min_notional_usdt:
            return canary.GateDecision(
                canary.GateVerdict.REJECT,
                f"notional {notional_usdt} below min_notional_usdt {min_notional_usdt}",
            )
        if notional_usdt > limits.max_order_usdt:
            return canary.GateDecision(
                canary.GateVerdict.REJECT,
                f"notional {notional_usdt} exceeds max_order_usdt {limits.max_order_usdt}",
            )
        if state.cumulative_usdt + notional_usdt > limits.max_total_usdt:
            return canary.GateDecision(
                canary.GateVerdict.REJECT,
                f"cumulative {state.cumulative_usdt}+{notional_usdt} exceeds "
                f"max_total_usdt {limits.max_total_usdt}",
            )
        if state.fills >= limits.max_fills:
            return canary.GateDecision(
                canary.GateVerdict.REJECT, f"max_fills {limits.max_fills} reached"
            )
        return canary.GateDecision(canary.GateVerdict.ALLOW, "ok")

    monkeypatch.setattr(canary, "evaluate_pretrade_gate", _patched_eval)

    def measure() -> None:
        for _ in range(10_000):
            canary.evaluate_pretrade_gate(
                symbol="BTC/USDT", notional_usdt=Decimal("1"), limits=limits, state=state
            )

    # 10배 지연된 함수는 1,000ms 예산을 초과해야 한다 — 그렇지 않으면
    # 이 테스트가 허울 단언임을 의미(검출력 결함).
    # budget_ms를 기존과 동일하게 1,000ms로 유지하고 실패를 기대한다.
    sample = perf_budget.sample(measure, batch=1)
    assert sample.cpu_ms >= 1000.0, (
        f"검출력 실패: 10배 CPU 부하 주입에도 예산 1,000ms를 초과하지 못함 — "
        f"cpu={sample.cpu_ms:.1f}ms. 단언이 허울임을 의미."
    )
