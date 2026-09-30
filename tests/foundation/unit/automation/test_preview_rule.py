from __future__ import annotations

from decimal import Decimal

import pytest

from src.foundation.automation.application import preview_rule as preview_rule_module
from src.foundation.automation.application.preview_rule import preview_rule
from src.foundation.automation.contracts.v1 import (
    InvalidRuleDefinitionError,
    PriceCondition,
    TimeCondition,
)
from src.foundation.automation.flags import FEATURE_FLAG_NAME, RuleEngineFeatureDisabledError

from .conftest import make_candle


def _bars(closes: list[str]) -> list:
    return [make_candle(close=Decimal(c), index=i) for i, c in enumerate(closes)]


def test_preview_counts_deterministic_triggers() -> None:
    # 90, 110, 90, 110, 90 -> ">100"은 인덱스 1·3에서 발동 = 2회.
    bars = _bars(["90", "110", "90", "110", "90"])
    condition = (PriceCondition(symbol="005930", operator=">", threshold=Decimal("100")),)

    result = preview_rule(condition, {"005930": bars})

    assert result.trigger_count == 2
    assert result.evaluated_bars == 5
    assert result.first_trigger_at == bars[1].open_time
    assert result.last_trigger_at == bars[3].open_time


def test_preview_is_deterministic_on_recompute() -> None:
    """U-4 DoD: 저장 시점 미리보기 값과 재실행 값이 일치(결정론)."""
    bars = _bars(["90", "110", "90", "110", "90"])
    condition = (PriceCondition(symbol="005930", operator=">", threshold=Decimal("100")),)

    first = preview_rule(condition, {"005930": bars})
    second = preview_rule(condition, {"005930": bars})

    assert first == second
    assert first.data_fingerprint == second.data_fingerprint


def test_preview_rejects_time_only_condition() -> None:
    from datetime import time

    with pytest.raises(InvalidRuleDefinitionError):
        preview_rule((TimeCondition(at=time(9, 0)),), {})


def test_preview_rejects_misaligned_symbol_bar_counts() -> None:
    condition = (
        PriceCondition(symbol="005930", operator=">", threshold=Decimal("100")),
        PriceCondition(symbol="000660", operator=">", threshold=Decimal("50")),
    )
    bars_by_symbol = {
        "005930": _bars(["90", "110"]),
        "000660": _bars(["10"]),
    }
    with pytest.raises(InvalidRuleDefinitionError, match="심볼별 봉 개수"):
        preview_rule(condition, bars_by_symbol)


def test_preview_rejects_when_feature_flag_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    """U-4a: FF_U4A_RULE_ENGINE이 꺼지면 미리보기도 즉시 거부한다(fail-closed)."""
    monkeypatch.delenv(FEATURE_FLAG_NAME, raising=False)
    bars = _bars(["90", "110"])
    condition = (PriceCondition(symbol="005930", operator=">", threshold=Decimal("100")),)

    with pytest.raises(RuleEngineFeatureDisabledError):
        preview_rule(condition, {"005930": bars})


def test_preview_propagates_evaluation_dependency_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    """실패주입: 조건 평가 의존성이 예외를 내면 위장 성공 없이 그대로 전파한다."""

    def _boom(*_args: object, **_kwargs: object) -> bool:
        raise RuntimeError("evaluate_conditions dependency failure (injected)")

    monkeypatch.setattr(preview_rule_module, "evaluate_conditions", _boom)
    bars = _bars(["90", "110"])
    condition = (PriceCondition(symbol="005930", operator=">", threshold=Decimal("100")),)

    with pytest.raises(RuntimeError, match="injected"):
        preview_rule(condition, {"005930": bars})
