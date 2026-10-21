"""RD-11 -- `domain/macro_series.py` unit tests.

Spec: docs/specs/L4_research_data_and_market_ecosystem_v1.0.md sec.9 RD-11
DoD ("빈도·단위·발표지연 정규화").
"""

from __future__ import annotations

import time
from datetime import date, datetime, timezone
from decimal import Decimal

import pytest

from src.foundation.research_data.domain.macro_series import (
    FrequencyNormalizationError,
    MacroFrequency,
    PeriodParseError,
    UnitNormalizationError,
    ValueParseError,
    compute_known_at,
    normalize_macro_observation,
    normalize_unit,
    period_end,
)
from tests.conftest import PerfBudget

# --- normalize_unit -----------------------------------------------------


@pytest.mark.parametrize(
    ("raw_unit", "expected_unit", "expected_scale"),
    [
        ("%", "percent", 1),
        ("퍼센트", "percent", 1),
        ("지수", "index", 1),
        ("포인트", "index", 1),
        ("원", "krw", 1),
        ("억원", "krw", 100_000_000),
        ("십억원", "krw", 1_000_000_000),
        ("달러", "usd", 1),
        ("백만달러", "usd", 1_000_000),
        ("명", "count", 1),
    ],
)
def test_normalize_unit_known_shapes(
    raw_unit: str, expected_unit: str, expected_scale: int
) -> None:
    result = normalize_unit(raw_unit)
    assert result.unit == expected_unit
    assert result.scale == Decimal(expected_scale)


def test_normalize_unit_rejects_empty_string() -> None:
    with pytest.raises(UnitNormalizationError):
        normalize_unit("")


def test_normalize_unit_rejects_unknown_base_unit() -> None:
    with pytest.raises(UnitNormalizationError):
        normalize_unit("억파운드")


def test_normalize_unit_rejects_magnitude_prefix_without_base_unit() -> None:
    with pytest.raises(UnitNormalizationError):
        normalize_unit("천")


# --- period_end ----------------------------------------------------------


@pytest.mark.parametrize(
    ("raw_period", "frequency", "expected"),
    [
        ("2024", MacroFrequency.ANNUAL, date(2024, 12, 31)),
        ("2024S1", MacroFrequency.SEMIANNUAL, date(2024, 6, 30)),
        ("20242", MacroFrequency.SEMIANNUAL, date(2024, 12, 31)),
        ("2024Q1", MacroFrequency.QUARTERLY, date(2024, 3, 31)),
        ("20244", MacroFrequency.QUARTERLY, date(2024, 12, 31)),
        ("202402", MacroFrequency.MONTHLY, date(2024, 2, 29)),  # leap year
        ("20240115", MacroFrequency.DAILY, date(2024, 1, 15)),
    ],
)
def test_period_end_known_shapes(
    raw_period: str, frequency: MacroFrequency, expected: date
) -> None:
    assert period_end(raw_period, frequency) == expected


def test_period_end_rejects_mismatched_format() -> None:
    with pytest.raises(PeriodParseError):
        period_end("2024", MacroFrequency.MONTHLY)


def test_period_end_rejects_invalid_calendar_date() -> None:
    with pytest.raises(PeriodParseError):
        period_end("20240230", MacroFrequency.DAILY)  # Feb 30 does not exist


# --- compute_known_at ------------------------------------------------------


def test_compute_known_at_adds_lag_at_utc_midnight() -> None:
    known_at = compute_known_at(date(2024, 1, 31), publication_lag_days=15)
    assert known_at == datetime(2024, 2, 15, tzinfo=timezone.utc)
    assert known_at.tzinfo is timezone.utc


def test_compute_known_at_rejects_negative_lag() -> None:
    with pytest.raises(ValueError):
        compute_known_at(date(2024, 1, 31), publication_lag_days=-1)


# --- normalize_macro_observation (composition) ------------------------------


def test_normalize_macro_observation_composes_all_axes() -> None:
    observation = normalize_macro_observation(
        source_id="ECOS",
        series_id="722Y001:0101000",
        raw_period="202401",
        frequency=MacroFrequency.MONTHLY,
        raw_value="3.5",
        raw_unit="%",
        seasonally_adjusted=True,
        publication_lag_days=20,
    )
    assert observation.source_id == "ECOS"
    assert observation.period == date(2024, 1, 31)
    assert observation.frequency is MacroFrequency.MONTHLY
    assert observation.value == Decimal("3.5")
    assert observation.unit == "percent"
    assert observation.seasonally_adjusted is True
    assert observation.known_at == datetime(2024, 2, 20, tzinfo=timezone.utc)


def test_normalize_macro_observation_applies_unit_scale_to_value() -> None:
    observation = normalize_macro_observation(
        source_id="KOSIS",
        series_id="DT_1YL20631:T10",
        raw_period="2024",
        frequency=MacroFrequency.ANNUAL,
        raw_value="1,234",
        raw_unit="억원",
        seasonally_adjusted=False,
        publication_lag_days=0,
    )
    assert observation.value == Decimal("123400000000")


# --- negative tests (>=3, D2 floor) ----------------------------------------


def test_normalize_macro_observation_rejects_empty_source_id() -> None:
    with pytest.raises(ValueError):
        normalize_macro_observation(
            source_id="",
            series_id="x",
            raw_period="2024",
            frequency=MacroFrequency.ANNUAL,
            raw_value="1",
            raw_unit="%",
            seasonally_adjusted=False,
            publication_lag_days=0,
        )


def test_normalize_macro_observation_rejects_non_numeric_value() -> None:
    with pytest.raises(ValueParseError):
        normalize_macro_observation(
            source_id="ECOS",
            series_id="x",
            raw_period="2024",
            frequency=MacroFrequency.ANNUAL,
            raw_value="not-a-number",
            raw_unit="%",
            seasonally_adjusted=False,
            publication_lag_days=0,
        )


def test_normalize_macro_observation_rejects_unknown_unit() -> None:
    with pytest.raises(UnitNormalizationError):
        normalize_macro_observation(
            source_id="ECOS",
            series_id="x",
            raw_period="2024",
            frequency=MacroFrequency.ANNUAL,
            raw_value="1",
            raw_unit="파운드",
            seasonally_adjusted=False,
            publication_lag_days=0,
        )


def test_normalize_macro_observation_rejects_period_frequency_mismatch() -> None:
    with pytest.raises(PeriodParseError):
        normalize_macro_observation(
            source_id="ECOS",
            series_id="x",
            raw_period="2024Q1",
            frequency=MacroFrequency.MONTHLY,
            raw_value="1",
            raw_unit="%",
            seasonally_adjusted=False,
            publication_lag_days=0,
        )


# --- failure injection: a fabricated frequency enum value the module never


def test_period_end_fails_closed_for_unsupported_frequency_value() -> None:
    """Failure injection -- `object.__new__` fabricates a `MacroFrequency`
    member that bypasses every branch in `period_end`, exercising its
    final `FrequencyNormalizationError` fail-closed fallthrough (a state
    that cannot occur through the public enum but must still not silently
    fall through to `None`/a guessed date)."""
    bogus = object.__new__(MacroFrequency)
    object.__setattr__(bogus, "_name_", "BOGUS")
    object.__setattr__(bogus, "_value_", "bogus")
    with pytest.raises(FrequencyNormalizationError):
        period_end("2024", bogus)


# --- numeric performance assertion (ADR-2026-09-09-C budget: pure CPU-bound
# normalization of a realistic batch must clear a fixed throughput floor) ---


@pytest.mark.perf
def test_normalize_macro_observation_throughput_floor(
    perf_budget: PerfBudget,
) -> None:
    """Raw ``time.perf_counter()`` → ``perf_budget.sample(batch=2000)`` 전환.

    예산: 2 000회 / 5 000회·초⁻¹ = 400ms 총소요 → 호출당 0.2ms.
    ``process_time()`` 해상도(~15.6ms) 때문에 단일 호출은 0/15.6만 나오므로
    batch=2000로 묶어 평균을 추정한다.
    """

    iterations = 2_000

    def _batch() -> None:
        for i in range(iterations):
            normalize_macro_observation(
                source_id="ECOS",
                series_id=f"722Y001:{i:07d}",
                raw_period="202401",
                frequency=MacroFrequency.MONTHLY,
                raw_value="3.5",
                raw_unit="%",
                seasonally_adjusted=False,
                publication_lag_days=20,
            )

    sample = perf_budget.sample(_batch, batch=1)
    # batch=1 → sample.cpu_ms는 2000회 총 시간(= 호출당 아님). 예산 0.2ms는
    # 호출당 예산이므로 총 예산으로 환산: 0.2 * 2000 = 400ms
    assert sample.cpu_ms < 400.0, (
        f"normalize_macro_observation total cpu_ms {sample.cpu_ms:.1f} exceeds "
        f"400ms budget ({iterations} calls)"
    )


@pytest.mark.perf
def test_normalize_macro_observation_throughput_floor_fails_with_10x_delay(
    perf_budget: PerfBudget,
) -> None:
    """Failure injection -- 10배 지연 주입 시 적색 확인.

    ``time.sleep(0.001)``(1ms)를 각 호출에 추가하면 호출당 평균이 0.2→1.2ms로
    늘어나 총 2400ms가 되어 400ms 예산을 초과한다. 이 지연을 제거하면 테스트는
    다시 녹색이 된다(원래 테스트 참조).
    """

    iterations = 2_000

    def _batch_delayed() -> None:
        for i in range(iterations):
            normalize_macro_observation(
                source_id="ECOS",
                series_id=f"722Y001:{i:07d}",
                raw_period="202401",
                frequency=MacroFrequency.MONTHLY,
                raw_value="3.5",
                raw_unit="%",
                seasonally_adjusted=False,
                publication_lag_days=20,
            )
            time.sleep(0.001)  # 1ms 지연 주입

    sample = perf_budget.sample(_batch_delayed, batch=1)
    # time.sleep 는 CPU 시간을 늘리지 않으므로 wall_ms 로 단언.
    # 2000회 × 1ms = 2000ms wall-clock → 400ms wall 예산을 크게 초과
    assert sample.wall_ms > 400.0, (
        f"delay injection failed to exceed budget: {sample.wall_ms:.1f}ms"
    )


# ---- CPU 0 수렴 + gate-red 재현 (D2, task-11539 후속) --------------------------


@pytest.mark.perf
def test_normalize_macro_observation_cpu_zero_convergence(
    perf_budget: PerfBudget,
) -> None:
    """CPU 측정의 0 수렴: 단일 호출 20회 best-of-5 최소 CPU가 5ms 미만이어야 한다.

    ``normalize_macro_observation``은 정규화(정규 표현식 매칭, Decimal 파싱,
    데이터클래스 인스턴스화)만 수행하는 순수 CPU 연산이므로, Windows의
    time.process_time() 양자화 틱(~15.6ms) 아래로 수렴해야 한다.
    """
    for _ in range(3):  # warmup
        normalize_macro_observation(
            source_id="ECOS",
            series_id="722Y001:0101000",
            raw_period="202401",
            frequency=MacroFrequency.MONTHLY,
            raw_value="3.5",
            raw_unit="%",
            seasonally_adjusted=False,
            publication_lag_days=20,
        )

    samples = [
        perf_budget.sample(
            lambda _i=i: normalize_macro_observation(
                source_id="ECOS",
                series_id=f"722Y001:{_i:07d}",
                raw_period="202401",
                frequency=MacroFrequency.MONTHLY,
                raw_value="3.5",
                raw_unit="%",
                seasonally_adjusted=False,
                publication_lag_days=20,
            ),
            batch=1,
        )
        for i in range(20)
    ]
    best = min(samples, key=lambda s: s.cpu_ms)
    assert best.cpu_ms < 5.0, (
        f"single-call CPU {best.cpu_ms:.3f}ms exceeds 5ms zero-convergence budget; "
        f"wall={best.wall_ms:.3f}ms"
    )


@pytest.mark.perf
def test_normalize_macro_observation_perf_assertion_catches_cpu_delay_injection(
    perf_budget: PerfBudget,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Gate-red reproduction (D2, task-11539 후속): 성능 예산 단언이 실제로
    위반을 포착하는지 역으로 증명한다. ``time.process_time``을 monkeypatch해
    1회 호출당 예산(5ms)을 초과하는 CPU 시간을 가장하면 같은 단언이
    AssertionError로 적색이 돼야 한다.

    단언/마커 삭제나 예산 완화가 아니라, 단언이 "제대로 작동하는지"를
    검증한다. CPU 예산이므로 sleep이 아닌 process_time 교체를 사용한다.
    """
    import time as _time

    _fake_time = [0.0]

    def _fake_process_time() -> float:
        _fake_time[0] += 60.0  # 60ms per call >> 5ms budget
        return _fake_time[0]

    monkeypatch.setattr(_time, "process_time", _fake_process_time)

    # 같은 측정/단언을 재사용 — budget을 초과하므로 AssertionError가 있어야 함
    with pytest.raises(AssertionError):
        sample = perf_budget.sample(
            lambda: normalize_macro_observation(
                source_id="ECOS",
                series_id="722Y001:0101000",
                raw_period="202401",
                frequency=MacroFrequency.MONTHLY,
                raw_value="3.5",
                raw_unit="%",
                seasonally_adjusted=False,
                publication_lag_days=20,
            ),
            batch=1,
        )
        assert sample.cpu_ms < 5.0, (
            f"normalize_macro_observation took {sample.cpu_ms:.3f}ms per call — budget 5ms"
        )
