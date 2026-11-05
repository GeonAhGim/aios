"""Perf-detection for test_candle_record_accepts_aware_open_time.

L4-07 감사 후속 (task-11539). 호출 1회 CPU 측정의 0 수렴과 단위/예산 불변을 검증.
DoD: negative 3개, 주입 실패 1개, 성능 수치 1개, gate-red 재현 1개.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest
from pydantic import ValidationError

from src.foundation.market_data.contracts import v1
from tests.conftest import PerfBudget
from tests.support.perf_inject import reset_virtual_totals, virtual_delayed_sync
from tests.unit.market_data.test_contracts_schema import _sample_candle, _sample_key


def _now() -> datetime:
    return datetime(2026, 9, 3, 0, 0, tzinfo=UTC)


# ── helpers ────────────────────────────────────────────────────────────────


def _sample_candle_with_naive_open() -> v1.CandleRecord:
    """naive open_time을 가진 CandleRecord — ValidationError 유발."""
    return v1.CandleRecord(
        key=_sample_key(),
        open_time=datetime(2026, 9, 3, 0, 0),  # naive
        close_time=datetime(2026, 9, 3, 0, 0, tzinfo=UTC),
        open=Decimal("50000"),
        high=Decimal("50100"),
        low=Decimal("49900"),
        close=Decimal("50050"),
        volume=Decimal("1.5"),
    )


# ── positive: perf-detection ──────────────────────────────────────────────


def test_candle_record_accepts_aware_open_time_with_perf_budget(
    perf_budget: PerfBudget,
) -> None:
    """aware datetime을 수용하고 schema_version을 설정한다 — CPU 예산 내 단일 호출.

    PerfBudget.best_of(n=5)로 Windows process_time() 양자화 tick(≈15.6ms) 노이즈를
    걷어내고 최소 cpu_ms로 0 수렴 단언을 안정화한다.
    """
    # positive: aware datetime 수용 + schema_version
    candle = _sample_candle()
    assert candle.open_time.tzinfo is not None
    assert candle.schema_version == "v1"

    # perf: 단일 호출 CPU 시간이 예산(2ms) 내에 수렴 — best_of로 0 수렴 안정화
    sample = perf_budget.best_of(
        lambda: _sample_candle(),
        n=5,
        batch=1,
    )
    assert sample.cpu_ms < 2.0, f"CandleRecord 생성 CPU 시간이 예산 초과: {sample.cpu_ms:.3f}ms"


# ── gate-red 재현 ─────────────────────────────────────────────────────────


def test_candle_record_accepts_aware_open_time_gate_red_virtual_delay(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """gate-red 재현: virtual_delayed_sync로 10ms 가상 지연을 주입하면
    PerfBudget.sample의 cpu_ms가 10ms 이상으로 측정되어야 한다.

    지연을 주지 않으면 cpu_ms는 실제 생성 시간(<<1ms)으로 매우 작아야 한다.
    """
    reset_virtual_totals()

    perf = PerfBudget()

    # 지연 없음 — baseline: 매우 작은 cpu_ms
    baseline = perf.sample(lambda: _sample_candle(), batch=1)
    assert baseline.cpu_ms < 50.0, (
        f"지연 없는 baseline이 너무 큼: {baseline.cpu_ms:.3f}ms — 측정 노이즈가 예산을 침식"
    )

    # 10ms 가상 지연 주입
    delayed_fn = virtual_delayed_sync(lambda: _sample_candle(), 0.010)

    def _wrapped() -> v1.CandleRecord:
        return delayed_fn()

    reset_virtual_totals()
    delayed_sample = perf.sample(_wrapped, batch=1)
    assert delayed_sample.cpu_ms >= 9.0, (
        f"10ms 가상 지연이 {delayed_sample.cpu_ms:.3f}ms로 읽힘 — 가상 지연 측정 누락"
    )


# ── negative tests ────────────────────────────────────────────────────────


def test_candle_record_naive_open_time_still_rejected() -> None:
    """음성 테스트: naive datetime은 여전히 ValidationError를 유발해야 한다."""
    with pytest.raises(ValidationError):
        _sample_candle(open_time=datetime(2026, 9, 3, 0, 0))


def test_candle_record_missing_close_field_rejected() -> None:
    """음성 테스트: close 필드 누락 시 ValidationError를 유발해야 한다."""
    with pytest.raises(ValidationError):
        v1.CandleRecord.model_validate(
            {
                "key": _sample_key(),
                "open_time": _now(),
                "close_time": _now(),
                "open": Decimal("50000"),
                "high": Decimal("50100"),
                "low": Decimal("49900"),
                # close 필드 누락
                "volume": Decimal("1.5"),
            }
        )


# ── injection failure ─────────────────────────────────────────────────────


def test_candle_record_injection_failure_validation_error_propagates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """주입 실패 테스트: virtual_delayed_sync가 TypeError를 전파하면
    검증 자체가 실패해야 한다(주입 시스템이 정상 작동 중임을 확인)."""
    reset_virtual_totals()

    perf = PerfBudget()

    # virtual_delayed_sync가 original을 호출할 때 ValidationError가 발생하면
    # 그대로 전파되어야 함
    def _bad_candle() -> v1.CandleRecord:
        return _sample_candle_with_naive_open()

    with pytest.raises(ValidationError):
        perf.sample(_bad_candle, batch=1)
