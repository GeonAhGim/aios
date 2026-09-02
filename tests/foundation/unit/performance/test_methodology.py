"""Performance 방법론 해시 — R2 "어느 버전으로 이 숫자가 나왔는가"의 기반.

Spec: docs/specs/L4_strategy_portfolio_backtest_v1.0.md §8 (L45 DoD)."""
from __future__ import annotations

from dataclasses import replace
from decimal import Decimal

from src.foundation.performance.domain.methodology import (
    DEFAULT_METHODOLOGY,
    methodology_hash,
)


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
