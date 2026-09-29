"""Performance 방법론 해시 — R2 "어느 버전으로 이 숫자가 나왔는가"의 기반.

Spec: docs/specs/L4_strategy_portfolio_backtest_v1.0.md §8 (L45 DoD)."""

from __future__ import annotations

import statistics
import time
from dataclasses import replace
from decimal import Decimal

import pytest

from src.foundation.performance.domain import methodology as methodology_module
from src.foundation.performance.domain.methodology import (
    DEFAULT_METHODOLOGY,
    MethodologyValidationError,
    methodology_hash,
)

# ADR-2026-09-09-C Decision 1에는 순수 해싱 함수 전용 예산이 없어, 월간
# 명세서 계산(성과 계산 p95 < 3s)보다 충분히 작은 5ms를 단위 예산으로 둔다.
_HASH_LATENCY_BUDGET_MS = 5


def test_default_methodology_hash_is_stable_and_non_self_referential():
    """methodology_hash 필드 자체를 입력에 넣지 않는다 — 넣었다면 매번
    다른 해시가 나오는 순환 문제가 생긴다."""
    first = methodology_hash(DEFAULT_METHODOLOGY)
    second = methodology_hash(DEFAULT_METHODOLOGY)
    assert first == second
    assert DEFAULT_METHODOLOGY.methodology_hash == first


def test_methodology_hash_changes_when_a_defining_field_changes():
    changed = replace(DEFAULT_METHODOLOGY, risk_free_rate_pct=Decimal("1"))
    assert methodology_hash(changed) != DEFAULT_METHODOLOGY.methodology_hash


def test_default_methodology_uses_zero_risk_free_rate():
    """무위험수익률 0 고정 — methodology.py 상단 주석의 정책 결정."""
    assert DEFAULT_METHODOLOGY.risk_free_rate_pct == Decimal("0")


@pytest.mark.parametrize("periods_per_year", [0, -1, -252])
def test_methodology_hash_rejects_non_positive_periods_per_year(periods_per_year):
    """암묵적 연환산 가정 금지(모듈 docstring) — periods_per_year<=0은 정의되지 않는다."""
    invalid = replace(DEFAULT_METHODOLOGY, periods_per_year=periods_per_year)
    with pytest.raises(MethodologyValidationError) as excinfo:
        methodology_hash(invalid)
    assert excinfo.value.reason_code == "INTEGRITY_METHODOLOGY_PERIODS_PER_YEAR"


@pytest.mark.parametrize(
    "risk_free_rate_pct",
    [Decimal("NaN"), Decimal("Infinity"), Decimal("-Infinity")],
)
def test_methodology_hash_rejects_non_finite_risk_free_rate(risk_free_rate_pct):
    """Decimal 정밀도 불변식(rules.assert_precision과 동일 기준) — 비유한값을 거부한다."""
    invalid = replace(DEFAULT_METHODOLOGY, risk_free_rate_pct=risk_free_rate_pct)
    with pytest.raises(MethodologyValidationError) as excinfo:
        methodology_hash(invalid)
    assert excinfo.value.reason_code == "INTEGRITY_CURRENCY_PRECISION"


@pytest.mark.parametrize("field_name", ["version", "twr_method", "mwr_method"])
def test_methodology_hash_rejects_blank_defining_fields(field_name):
    """빈 값은 서로 다른 방법론을 같은 해시로 충돌시켜 WORM 버전 규칙을 깬다."""
    invalid = replace(DEFAULT_METHODOLOGY, **{field_name: "   "})
    with pytest.raises(MethodologyValidationError) as excinfo:
        methodology_hash(invalid)
    assert excinfo.value.reason_code == "INTEGRITY_METHODOLOGY_BLANK_FIELD"


def test_methodology_hash_propagates_serialization_failure(monkeypatch):
    """실패주입 — 직렬화 의존성 실패를 조용히 삼키지 않는다."""
    failure = TypeError("injected json.dumps failure")

    def _broken_dumps(*_args, **_kwargs):
        raise failure

    monkeypatch.setattr(methodology_module.json, "dumps", _broken_dumps)
    with pytest.raises(TypeError, match="injected json.dumps failure") as excinfo:
        methodology_hash(DEFAULT_METHODOLOGY)
    assert excinfo.value is failure


def test_methodology_hash_propagates_hashing_dependency_failure(monkeypatch):
    """실패주입 — 해시 의존성 실패도 상위로 전파해야 한다(조용한 대체 금지)."""
    failure = RuntimeError("injected hashlib.sha256 failure")

    def _broken_sha256(*_args, **_kwargs):
        raise failure

    monkeypatch.setattr(methodology_module.hashlib, "sha256", _broken_sha256)
    with pytest.raises(RuntimeError, match="injected hashlib.sha256 failure") as excinfo:
        methodology_hash(DEFAULT_METHODOLOGY)
    assert excinfo.value is failure


def _p95_latency_ms(samples: int = 50) -> float:
    latencies_ms: list[float] = []
    for _ in range(samples):
        start = time.perf_counter()
        methodology_hash(DEFAULT_METHODOLOGY)
        latencies_ms.append((time.perf_counter() - start) * 1000)
    return statistics.quantiles(latencies_ms, n=20)[18]  # p95


def test_methodology_hash_meets_latency_budget():
    """수치 성능 단언 — 방법론 해시 계산 p95가 예산 안이어야 한다."""
    assert _p95_latency_ms() < _HASH_LATENCY_BUDGET_MS


def test_methodology_hash_latency_budget_actually_fails_when_calculation_regresses(
    monkeypatch,
):
    """게이트 적색 재현 — 직렬화에 지연을 주입하면 성능 단언이 적색이어야 한다."""
    original_dumps = methodology_module.json.dumps

    def _slow_dumps(*args, **kwargs):
        time.sleep(0.01)
        return original_dumps(*args, **kwargs)

    monkeypatch.setattr(methodology_module.json, "dumps", _slow_dumps)

    latency_ms = _p95_latency_ms(samples=5)
    with pytest.raises(AssertionError):
        assert latency_ms < _HASH_LATENCY_BUDGET_MS
