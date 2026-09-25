"""L10 D2 증빙 -- src/core/strategy/models.py.

negative >= 3, 실패 주입 1, 성능 단언 1, 게이트 적색 재현 1
(ADR-2026-09-09-C Decision 1).

Spec: docs/specs/L4_strategy_portfolio_backtest_v1.0.md#§9 L10.

Signal은 순수 pydantic 스키마다 -- I/O도 커스텀 validator도 없고 필드
검증은 pydantic-core(Rust)가 전담한다. 따라서 "모듈 내부 의존성"을
monkeypatch로 깨뜨려 주입할 지점이 없다(경험적으로 확인: 모듈 네임스페이스의
Decimal/Enum을 monkeypatch해도 pydantic-core는 스키마 컴파일 시점에 캡처한
검증 로직을 그대로 쓰므로 영향이 없다). 대신 "상류(upstream) 의존성이
오염된 payload를 보낸 경우"를 monkeypatch로 주입해 이 경계가 fail-closed로
거부하는지 검증한다 -- Signal을 소비하는 FD-8.2 PortfolioEngine 입장에서
실제로 마주치는 실패 모드다.
"""

from __future__ import annotations

import sys
import time
from datetime import datetime, timezone
from decimal import Decimal

import pytest
from pydantic import ValidationError

from src.core.strategy.models import Signal
from src.data.models.strategy_fsm import FSMState
from src.data.models.trading import OrderSide

_AS_OF = datetime(2026, 1, 1, tzinfo=timezone.utc)


def _valid_kwargs() -> dict:
    return {
        "strategy_id": "strat-1",
        "strategy_version": "v1",
        "symbol": "BTC/USDT",
        "direction": OrderSide.BUY,
        "confidence": 0.8,
        "target_position": Decimal("0"),
        "stop_loss": Decimal("95"),
        "take_profit": Decimal("110"),
        "timestamp": _AS_OF,
        "to_state": FSMState.BUY_ORDER_PENDING,
    }


def _from_upstream_fsm_engine() -> dict:
    """FD-8.1 StrategyEngine.evaluate가 만들어 보내는 raw payload를 흉내낸다."""
    return _valid_kwargs()


# --- 정상 경로 ---------------------------------------------------------


def test_signal_constructs_with_all_fields_populated():
    signal = Signal(**_valid_kwargs())

    assert signal.strategy_id == "strat-1"
    assert signal.direction is OrderSide.BUY
    assert signal.target_position == Decimal("0")
    assert signal.stop_loss == Decimal("95")
    assert signal.take_profit == Decimal("110")
    assert signal.to_state is FSMState.BUY_ORDER_PENDING


def test_signal_allows_stop_loss_and_take_profit_to_be_none():
    kwargs = _valid_kwargs()
    kwargs["stop_loss"] = None
    kwargs["take_profit"] = None

    signal = Signal(**kwargs)

    assert signal.stop_loss is None
    assert signal.take_profit is None


def test_signal_accepts_enum_values_passed_as_plain_strings():
    kwargs = _valid_kwargs()
    kwargs["direction"] = "SELL"
    kwargs["to_state"] = "HOLDING"

    signal = Signal(**kwargs)

    assert signal.direction is OrderSide.SELL
    assert signal.to_state is FSMState.HOLDING


# --- negative (>= 3) ----------------------------------------------------


def test_negative_missing_required_field_rejected():
    kwargs = _valid_kwargs()
    del kwargs["target_position"]

    with pytest.raises(ValidationError, match="target_position"):
        Signal(**kwargs)


def test_negative_direction_outside_order_side_enum_rejected():
    kwargs = _valid_kwargs()
    kwargs["direction"] = "HOLD"  # OrderSide는 BUY/SELL만 존재

    with pytest.raises(ValidationError, match="direction"):
        Signal(**kwargs)


def test_negative_to_state_outside_fsm_state_enum_rejected():
    kwargs = _valid_kwargs()
    kwargs["to_state"] = "NONEXISTENT_STATE"

    with pytest.raises(ValidationError, match="to_state"):
        Signal(**kwargs)


def test_negative_confidence_wrong_type_rejected():
    kwargs = _valid_kwargs()
    kwargs["confidence"] = {"not": "a-float"}

    with pytest.raises(ValidationError, match="confidence"):
        Signal(**kwargs)


def test_negative_target_position_non_numeric_string_rejected():
    kwargs = _valid_kwargs()
    kwargs["target_position"] = "not-a-decimal"

    with pytest.raises(ValidationError, match="target_position"):
        Signal(**kwargs)


# --- 실패 주입 (1) --------------------------------------------------------


def test_failure_injection_upstream_payload_corrupted_to_state_fails_closed(monkeypatch):
    """FD-8.1 엔진(상류 의존성)이 오염된 to_state를 내보내는 경우를 흉내낸다
    -- FSM 전이 로직 버그나 부분 배포로 인해 존재하지 않는 상태 문자열이
    실려 올 수 있다. 이 경계는 이를 조용히 통과시키지 않고 즉시 거부해야
    한다(fail-closed)."""

    original = _from_upstream_fsm_engine

    def _corrupted_upstream() -> dict:
        payload = original()
        payload["to_state"] = "CORRUPTED_BY_UPSTREAM"
        return payload

    monkeypatch.setattr(sys.modules[__name__], "_from_upstream_fsm_engine", _corrupted_upstream)

    with pytest.raises(ValidationError):
        Signal(**_from_upstream_fsm_engine())


# --- 성능 단언 (1) --------------------------------------------------------


@pytest.mark.perf
def test_performance_signal_construction_p99_within_pretrade_gate_budget():
    """ADR-2026-09-09-C Decision 1 예산: 사전거래 게이트 p99 5ms.
    Signal은 FD-8.1 evaluate()의 직접 출력이므로 이 예산에 포함된다."""
    kwargs = _valid_kwargs()

    n = 1000
    latencies_ms: list[float] = []
    for _ in range(n):
        start = time.perf_counter()
        Signal(**kwargs)
        latencies_ms.append((time.perf_counter() - start) * 1000)

    latencies_ms.sort()
    p99 = latencies_ms[int(n * 0.99) - 1]
    assert p99 < 5.0, f"p99={p99:.3f}ms -- 사전거래 게이트 5ms 예산 초과"


# --- 게이트 적색 재현 (1) --------------------------------------------------


def test_gate_red_target_position_draft_value_not_distinguishable_from_real_zero():
    """§3.5 docstring 인용: target_position은 이 계층에서 "Draft 값(0)"으로만
    채워지고 실제 수량은 FD-8.2 PortfolioEngine(8.2-A Master Authority)이
    덮어쓴다. 즉 Signal 계층 자체는 "이 0이 draft placeholder인지, 전략이
    의도적으로 청산 없음을 뜻하는 진짜 0인지" 구분할 방법이 없다(적색 --
    이 클래스만 보고는 판별 불가). engine.py가 항상 같은 Decimal("0")을
    보내는 것이 정책적으로 안전한 이유는 8.2-A가 유일한 수량 결정권자이기
    때문이며, 그 배선을 검증하는 것은 이 리프(순수 스키마) 범위 밖이다
    -- FD-8.2 리프에서 별도로 다룬다."""
    draft = Signal(**{**_valid_kwargs(), "target_position": Decimal("0")})
    intentional_flat = Signal(**{**_valid_kwargs(), "target_position": Decimal("0")})

    assert draft.target_position == intentional_flat.target_position == Decimal("0")
    assert draft.model_dump() == intentional_flat.model_dump()
