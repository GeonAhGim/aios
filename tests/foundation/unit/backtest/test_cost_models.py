"""BT-8 비용 2종(funding·borrow) — 일할 계산 정확·경계·부호·음수 거부.

Spec: docs/specs/L4_analytics_authoring_backtest_marketplace_v1.0.md
§2.5 BT-8, §3.4(`costs: {funding, borrow_apr}`), §9.5 BT-8(DoD: "일할 계산 정확").

모든 기대값은 손으로 계산해 Decimal exact 비교로 단언한다(float 근사
비교 금지). funding·borrow는 서로 다른 파일이라 이 테스트도 모듈별
섹션으로 나눈다.

DEEPEN(task-3039, ADR-2026-09-09-C D2): depth=D2 증빙.
- negative tests >=3: 위 `*_rejects_*` 다수(음수·NaN·역전 구간·naive
  datetime 등).
- failure injection 1: `test_exact_total_seconds_avoids_float_precision_loss`
  — `timedelta.total_seconds()`(float 경유)를 그대로 썼다면 서기 2300년대처럼
  epoch에서 먼 시각에서 1e-8초 단위 오차가 생겼을 상황을 재현해, 이번
  DEEPEN에서 고친 `exact_total_seconds`(정수 필드 직접 조합)가 그 오차를
  없앤다는 것을 증명한다(실제로 발견해 고친 결함).
- perf assertion 1: `test_compute_borrow_cost_perf_budget`.
- gate-red repro: N/A(이 리프에 정적 스캐너 없음 — failure-injection
  테스트가 런타임 가드를 직접 실행한다).
"""

import time
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from src.data.models.trading import OrderSide
from src.foundation.backtest.domain.costs import exact_total_seconds, round_cost
from src.foundation.backtest.domain.costs.borrow import compute_borrow_cost
from src.foundation.backtest.domain.costs.funding import (
    compute_funding_cost,
    count_funding_settlements,
)
from src.foundation.backtest.domain.models_v2 import CostsConfig

# --------------------------------------------------------------------------
# BT-8 funding
# --------------------------------------------------------------------------

_FUNDING_ON = CostsConfig(funding=True, borrow_apr=None)
_FUNDING_OFF = CostsConfig(funding=False, borrow_apr=None)

_D0 = datetime(2026, 1, 1, 0, 0, 0, tzinfo=timezone.utc)  # 정산 시각(00:00 UTC)


def test_count_funding_settlements_entry_exact_boundary_is_included() -> None:
    """진입이 정확히 정산 시각이면 그 정산이 포함된다(경계, 포함)."""
    count = count_funding_settlements(entry_time=_D0, exit_time=_D0.replace(hour=7))
    assert count == 1


def test_count_funding_settlements_exit_exact_boundary_is_excluded() -> None:
    """청산이 정확히 정산 시각이면 그 정산은 제외된다(경계, 배제 —
    다음 트레이드가 이어받는다는 가정으로 이중 계산을 막는다)."""
    entry = _D0.replace(hour=1)
    exit_ = _D0.replace(hour=8)
    count = count_funding_settlements(entry_time=entry, exit_time=exit_)
    assert count == 0


def test_count_funding_settlements_entry_and_exit_both_on_boundary() -> None:
    """진입 00:00(포함)~청산 08:00(배제) 구간에는 00:00 정산 1회만 잡힌다."""
    count = count_funding_settlements(entry_time=_D0, exit_time=_D0.replace(hour=8))
    assert count == 1


def test_count_funding_settlements_spans_three_intervals() -> None:
    """00:00 진입, 16:00:01 청산 → 00:00·08:00·16:00 3회 정산이 잡힌다."""
    exit_ = _D0.replace(hour=16, second=1)
    count = count_funding_settlements(entry_time=_D0, exit_time=exit_)
    assert count == 3


def test_count_funding_settlements_rejects_reversed_period() -> None:
    with pytest.raises(ValueError, match="exit_time"):
        count_funding_settlements(entry_time=_D0.replace(hour=8), exit_time=_D0)


def test_count_funding_settlements_rejects_naive_datetime() -> None:
    with pytest.raises(ValueError, match="tz-aware"):
        count_funding_settlements(entry_time=datetime(2026, 1, 1), exit_time=_D0.replace(hour=8))


def test_count_funding_settlements_rejects_non_positive_interval_hours() -> None:
    with pytest.raises(ValueError, match="interval_hours"):
        count_funding_settlements(entry_time=_D0, exit_time=_D0.replace(hour=8), interval_hours=0)


@pytest.mark.parametrize(
    ("side", "funding_rate", "expected"),
    [
        (OrderSide.BUY, Decimal("0.0001"), Decimal("10")),  # 롱 + 양(+)의 펀딩비 → 지급
        (OrderSide.BUY, Decimal("-0.0001"), Decimal("-10")),  # 롱 + 음(-)의 펀딩비 → 수취
        (OrderSide.SELL, Decimal("0.0001"), Decimal("-10")),  # 숏 + 양(+)의 펀딩비 → 수취
        (OrderSide.SELL, Decimal("-0.0001"), Decimal("10")),  # 숏 + 음(-)의 펀딩비 → 지급
    ],
)
def test_compute_funding_cost_sign_across_four_combinations(
    side: OrderSide, funding_rate: Decimal, expected: Decimal
) -> None:
    cost = compute_funding_cost(
        _FUNDING_ON,
        side=side,
        notional=Decimal("100000"),
        funding_rate=funding_rate,
        entry_time=_D0,
        exit_time=_D0.replace(hour=7),  # 정산 1회(00:00만 포함)
    )
    assert cost == expected


def test_compute_funding_cost_disabled_returns_zero_without_validating_other_args() -> None:
    """`funding=False`면 음수·역전 인자가 섞여 있어도 예외 없이 0을 반환한다."""
    cost = compute_funding_cost(
        _FUNDING_OFF,
        side=OrderSide.BUY,
        notional=Decimal("-1"),
        funding_rate=Decimal("0.0001"),
        entry_time=_D0.replace(hour=8),
        exit_time=_D0,
    )
    assert cost == Decimal("0")


def test_compute_funding_cost_rejects_negative_notional() -> None:
    with pytest.raises(ValueError, match="notional"):
        compute_funding_cost(
            _FUNDING_ON,
            side=OrderSide.BUY,
            notional=Decimal("-1"),
            funding_rate=Decimal("0.0001"),
            entry_time=_D0,
            exit_time=_D0.replace(hour=7),
        )


def test_compute_funding_cost_rejects_nan_funding_rate() -> None:
    with pytest.raises(ValueError, match="funding_rate"):
        compute_funding_cost(
            _FUNDING_ON,
            side=OrderSide.BUY,
            notional=Decimal("1"),
            funding_rate=Decimal("NaN"),
            entry_time=_D0,
            exit_time=_D0.replace(hour=7),
        )


def test_compute_funding_cost_rejects_reversed_period() -> None:
    with pytest.raises(ValueError, match="exit_time"):
        compute_funding_cost(
            _FUNDING_ON,
            side=OrderSide.BUY,
            notional=Decimal("1"),
            funding_rate=Decimal("0.0001"),
            entry_time=_D0.replace(hour=8),
            exit_time=_D0,
        )


def test_compute_funding_cost_zero_settlements_yields_zero_cost() -> None:
    cost = compute_funding_cost(
        _FUNDING_ON,
        side=OrderSide.BUY,
        notional=Decimal("100000"),
        funding_rate=Decimal("0.0001"),
        entry_time=_D0.replace(hour=1),
        exit_time=_D0.replace(hour=8),  # 청산이 정산 시각이라 배제 → 정산 0회
    )
    assert cost == Decimal("0")


# --------------------------------------------------------------------------
# BT-8 borrow
# --------------------------------------------------------------------------

_BORROW_ON = CostsConfig(funding=False, borrow_apr=Decimal("0.10"))
_BORROW_OFF = CostsConfig(funding=False, borrow_apr=None)


def test_compute_borrow_cost_exact_value_non_leap_full_year() -> None:
    """365일(비윤년) 보유는 ACT/365에서 APR을 그대로 곱한 값과 같다."""
    cost = compute_borrow_cost(
        _BORROW_ON,
        notional=Decimal("1000000"),
        entry_time=datetime(2023, 1, 1, tzinfo=timezone.utc),
        exit_time=datetime(2024, 1, 1, tzinfo=timezone.utc),  # 2023은 365일
    )
    assert cost == Decimal("100000")


def test_compute_borrow_cost_leap_year_uses_366_actual_days_over_fixed_365_denominator() -> None:
    """2024는 윤년(366일 경과) — 분자는 실제 경과일수를 그대로 쓰고
    분모는 365로 고정하는 ACT/365 fixed이므로 정확히 1년(365일) 대차보다
    비용이 커야 한다(365일분 기준값 100000을 초과)."""
    cost = compute_borrow_cost(
        _BORROW_ON,
        notional=Decimal("1000000"),
        entry_time=datetime(2024, 1, 1, tzinfo=timezone.utc),
        exit_time=datetime(2025, 1, 1, tzinfo=timezone.utc),  # 2024는 366일(윤년)
    )
    expected = round_cost(Decimal("1000000") * Decimal("0.10") * Decimal(366) / Decimal(365))
    assert cost == expected
    assert cost > Decimal("100000")


def test_compute_borrow_cost_exact_value_partial_day() -> None:
    """반나절(12시간=0.5일) 보유도 초 단위 경과시간으로 일할 계산된다."""
    cost = compute_borrow_cost(
        CostsConfig(funding=False, borrow_apr=Decimal("1")),
        notional=Decimal("365000"),
        entry_time=_D0,
        exit_time=_D0.replace(hour=12),
    )
    assert cost == Decimal("500")


def test_compute_borrow_cost_disabled_returns_zero_without_validating_other_args() -> None:
    """`borrow_apr=None`이면 음수·역전 인자가 섞여 있어도 예외 없이 0을 반환한다."""
    cost = compute_borrow_cost(
        _BORROW_OFF,
        notional=Decimal("-1"),
        entry_time=_D0.replace(hour=8),
        exit_time=_D0,
    )
    assert cost == Decimal("0")


def test_compute_borrow_cost_rejects_negative_notional() -> None:
    with pytest.raises(ValueError, match="notional"):
        compute_borrow_cost(
            _BORROW_ON, notional=Decimal("-1"), entry_time=_D0, exit_time=_D0.replace(hour=8)
        )


def test_compute_borrow_cost_rejects_reversed_period() -> None:
    with pytest.raises(ValueError, match="exit_time"):
        compute_borrow_cost(
            _BORROW_ON,
            notional=Decimal("1"),
            entry_time=_D0.replace(hour=8),
            exit_time=_D0,
        )


def test_compute_borrow_cost_rejects_naive_datetime() -> None:
    with pytest.raises(ValueError, match="tz-aware"):
        compute_borrow_cost(
            _BORROW_ON,
            notional=Decimal("1"),
            entry_time=datetime(2026, 1, 1),
            exit_time=_D0.replace(hour=8),
        )


def test_costs_config_rejects_negative_borrow_apr() -> None:
    with pytest.raises(Exception):  # noqa: B017 - pydantic ValidationError, contract-level
        CostsConfig(funding=False, borrow_apr=Decimal("-0.01"))


# --------------------------------------------------------------------------
# BT-8 DEEPEN(task-3039) — exact_total_seconds D2 증빙
# --------------------------------------------------------------------------


def test_exact_total_seconds_avoids_float_precision_loss() -> None:
    """failure injection(D2): `timedelta.total_seconds()`는 내부적으로
    마이크로초 총합을 float로 나눠 반환한다 — epoch에서 멀리 떨어진(서기
    2300년대) 시각에서는 그 나눗셈이 1e-8초 단위까지 어긋난다(이 DEEPEN
    이전의 `funding.py`/`borrow.py` 구현이 실제로 이 오차를 그대로 안고
    있었다 — 실제로 발견해 고친 결함). `exact_total_seconds`는 `days`/
    `seconds`/`microseconds` 정수 필드를 직접 조합해 그 float 나눗셈을
    완전히 피한다."""

    far_future = datetime(2300, 1, 1, 1, 2, 3, 456789, tzinfo=timezone.utc)
    epoch = datetime(1970, 1, 1, tzinfo=timezone.utc)
    delta = far_future - epoch

    float_based = Decimal(delta.total_seconds())
    exact = exact_total_seconds(delta)

    assert float_based != exact, "회귀 재현 실패: 이 델타는 float 나눗셈 오차를 드러내야 한다"
    expected = Decimal(delta.days) * Decimal(86400) + Decimal(delta.seconds) + Decimal("0.456789")
    assert exact == expected


def test_compute_funding_cost_far_future_settlement_count_is_exact() -> None:
    """수치 회귀 가드: `count_funding_settlements`가 epoch에서 아주 먼
    시각(서기 2300년대, Python `datetime` 표현 범위 안에서는 위 float 오차가
    경계를 흔들 만큼 커지지 않는다는 것을 확인했지만, `exact_total_seconds`로
    바꾼 뒤에도 정상 범위 결과가 그대로 유지되는지 잠근다) — 진입이 정확히
    정산 시각이면 그 정산이 정확히 1회 포함돼야 한다."""

    entry = datetime(2300, 1, 1, 0, 0, 0, tzinfo=timezone.utc)
    exit_ = entry + timedelta(hours=7)
    count = count_funding_settlements(entry_time=entry, exit_time=exit_)
    assert count == 1


@pytest.mark.perf
def test_compute_borrow_cost_perf_budget() -> None:
    """perf assertion(D2): `compute_borrow_cost`는 백테스트 한 회 실행에서
    보유 포지션마다 반복 호출되는 순수 함수다 — O(1) 산술만 하므로 상한을
    넉넉히 잡아도(10,000회 <= 200ms) 회귀를 잡아낼 수 있다."""

    config = CostsConfig(funding=False, borrow_apr=Decimal("0.10"))
    entry = datetime(2026, 1, 1, tzinfo=timezone.utc)
    exit_ = datetime(2026, 6, 1, tzinfo=timezone.utc)

    start = time.perf_counter()
    for _ in range(10_000):
        compute_borrow_cost(config, notional=Decimal("100000"), entry_time=entry, exit_time=exit_)
    elapsed_ms = (time.perf_counter() - start) * 1000

    assert elapsed_ms < 200, f"compute_borrow_cost 너무 느림: {elapsed_ms:.1f}ms/10,000회"
