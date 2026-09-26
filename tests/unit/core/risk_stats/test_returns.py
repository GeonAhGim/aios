"""L4_risk_and_safety_v1.0.md#R-18 — returns.py 순수 함수 테스트.

D3 범위 노트(리뷰 task-8044 REJECT 후속): returns.py는 eventstore/DB에 닿지 않는 순수 함수
모듈이라 `scripts/replay_verify.py`(원장 이벤트 리플레이 해시 재현)가 문자 그대로 적용되지
않는다 — 대신 INVARIANTS.md I-05("백테스트와 라이브는 같은 컴파일 산출물·같은 도메인
로직을 공유")를 이 leaf에 맞게 재해석해, 동일 입력을 반복 호출해도 비트 단위로 동일한
출력이 나오는지("재현 일치")를 테스트한다(ADR-2026-09-10-C Decision 4에 따른 N/A 대체
근거).
"""

import time
from decimal import Decimal

import numpy as np
import pytest

from src.core.risk_stats.returns import bars_per_day, log_returns, scale_sigma


def test_bars_per_day_1m_is_1440():
    assert bars_per_day("1m") == 1440


def test_bars_per_day_1d_is_1():
    assert bars_per_day("1d") == 1


def test_bars_per_day_unsupported_timeframe_raises():
    with pytest.raises(ValueError):
        bars_per_day("7m")


def test_log_returns_length_is_n_minus_1():
    closes = [Decimal("100"), Decimal("101"), Decimal("99"), Decimal("102")]
    result = log_returns(closes)
    assert len(result) == len(closes) - 1


def test_log_returns_known_value():
    closes = [Decimal("100"), Decimal("110")]
    result = log_returns(closes)
    assert result == pytest.approx([np.log(1.1)])


def test_log_returns_single_close_returns_empty():
    result = log_returns([Decimal("100")])
    assert len(result) == 0


def test_scale_sigma_includes_bars_per_day_factor():
    # R4 회귀 방지: bars_per_day가 곱해지지 않으면 이 값과 달라진다.
    assert scale_sigma(0.01, bars_per_day=1440, horizon_days=1) == pytest.approx(0.01 * (1440**0.5))


def test_scale_sigma_daily_bars_horizon_one_is_identity():
    assert scale_sigma(0.02, bars_per_day=1, horizon_days=1) == pytest.approx(0.02)


def test_scale_sigma_negative_horizon_raises():
    with pytest.raises(ValueError):
        scale_sigma(0.02, bars_per_day=1440, horizon_days=-1)


def test_log_returns_empty_input_returns_empty():
    result = log_returns([])
    assert len(result) == 0


def test_bars_per_day_empty_string_raises():
    with pytest.raises(ValueError):
        bars_per_day("")


def test_log_returns_zero_price_yields_non_finite_not_silent_corruption():
    """적대적 입력: 0원 종가는 log(0) = -inf가 되어야 한다.

    I-07("검증/승인 게이트의 hard-fail 조건은 도메인 코드가 계산하고 실제로 FAIL을
    반환할 수 있어야 한다") 교차검증: 0/음수 가격이 들어와도 이 함수가 유효해 보이는
    유한값을 조용히 반환하면, 하류 리스크 게이트가 오염된 수익률을 정상 데이터처럼
    통과시켜 fail-closed 원칙(CLAUDE.md §3)을 무력화한다. 이 테스트는 그 대신
    비정상 신호(-inf)가 그대로 드러나는지 고정한다.
    """
    closes = [Decimal("100"), Decimal("0"), Decimal("50")]
    with np.errstate(divide="ignore"):
        result = log_returns(closes)
    assert result[0] == -np.inf
    # -inf가 다음 diff로 전파돼 +inf가 되어도 여전히 non-finite로 남아 하류에서
    # 유효값처럼 통과할 수 없다 — 조용한 오염이 아니라 감지 가능한 신호로 남는다.
    assert not np.isfinite(result[1])


def test_log_returns_negative_price_yields_nan_not_silent_corruption():
    """적대적 입력: 음수 종가는 NaN이 되어야 한다(허수 로그를 유한값으로 위장하지 않음)."""
    closes = [Decimal("100"), Decimal("-10")]
    with np.errstate(invalid="ignore"):
        result = log_returns(closes)
    assert np.isnan(result[0])


def test_log_returns_replay_reproduction_is_bit_identical():
    """D3 재현 일치(리뷰 task-8044 REJECT 후속, 모듈 docstring 참조).

    scripts/replay_verify.py는 eventstore를 리플레이해 해시 일치를 확인하지만
    returns.py는 순수 함수라 이벤트가 없다. 대신 I-05(백테스트/라이브가 같은 도메인
    로직을 공유)를 이 leaf에 적용해, 같은 종가 시퀀스를 반복 호출("replay")해도
    비트 단위로 동일한 배열이 나오는지 고정한다 — 같은 리스크 계산이 백테스트 재현과
    라이브 실행에서 다른 값을 내면 I-05가 깨진다.
    """
    closes = [Decimal("100"), Decimal("101.5"), Decimal("98.25"), Decimal("102.75")]
    first = log_returns(closes)
    second = log_returns(closes)
    assert np.array_equal(first, second)


def test_scale_sigma_replay_reproduction_is_identical():
    first = scale_sigma(0.0137, bars_per_day=288, horizon_days=5)
    second = scale_sigma(0.0137, bars_per_day=288, horizon_days=5)
    assert first == second


def test_log_returns_throughput_100k_bars_under_budget():
    """수치 성능 단언: 이 leaf의 예산 표(ADR-2026-09-09-C Decision 1)에는 risk_stats
    순수 함수 항목이 없다 — 가장 가까운 이웃(지표 증분=일괄 동일, 백테스트 1개월 M1
    3초)에 견줘, 10만 봉 배열 처리는 그보다 훨씬 가벼운 순수 numpy 연산이므로 여유
    있는 자체 임계값(0.2s, process_time 기준)을 건다(task-8356 관례: wall-clock 대신
    process_time으로 CI 잡노이즈 회피).
    """
    closes = [Decimal(str(100 + (i % 7) * 0.1)) for i in range(100_000)]
    start = time.process_time()
    result = log_returns(closes)
    elapsed = time.process_time() - start
    assert len(result) == len(closes) - 1
    assert elapsed < 0.2
