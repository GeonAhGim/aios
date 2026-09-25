"""76번 §1/§3/§6 규칙의 단위테스트 — DB 없이 순수 함수만 검증한다."""

import time
from datetime import datetime, timezone
from decimal import Decimal

import pytest
from pydantic import ValidationError

from src.data.models.market_data import Candle
from src.foundation.validation.domain.check_result import CheckResult
from src.foundation.validation.domain.models import Outcome
from src.foundation.validation.domain.rules import (
    compute_input_snapshot_hash,
    compute_result_hash,
    evaluate_bundle,
    evaluate_validation_policy,
)

NOW = datetime(2026, 9, 2, tzinfo=timezone.utc)


def _bar(close: str = "100") -> Candle:
    return Candle(
        symbol="BTC/USDT",
        exchange="bitget",
        timeframe="1h",
        open_time=NOW,
        close_time=NOW,
        open=Decimal(close),
        high=Decimal(close),
        low=Decimal(close),
        close=Decimal(close),
        volume=Decimal("1"),
    )


def _snapshot_kwargs(**overrides):
    defaults = dict(
        fsm_definition={"states": ["IDLE"]},
        cost_model={"fee_bps": "5", "slippage_bps": "2"},
        warmup_bars=0,
        periods_per_year=252,
        initial_equity=Decimal("10000"),
        bars=[_bar()],
    )
    defaults.update(overrides)
    return defaults


def test_snapshot_hash_is_stable_for_identical_input():
    a = compute_input_snapshot_hash(**_snapshot_kwargs())
    b = compute_input_snapshot_hash(**_snapshot_kwargs())
    assert a == b


def test_snapshot_hash_changes_when_fsm_definition_changes():
    a = compute_input_snapshot_hash(**_snapshot_kwargs())
    b = compute_input_snapshot_hash(**_snapshot_kwargs(fsm_definition={"states": ["OTHER"]}))
    assert a != b


def test_snapshot_hash_changes_when_bars_change():
    a = compute_input_snapshot_hash(**_snapshot_kwargs())
    b = compute_input_snapshot_hash(**_snapshot_kwargs(bars=[_bar("200")]))
    assert a != b


def test_snapshot_hash_changes_when_cost_model_changes():
    a = compute_input_snapshot_hash(**_snapshot_kwargs())
    b = compute_input_snapshot_hash(
        **_snapshot_kwargs(cost_model={"fee_bps": "10", "slippage_bps": "2"})
    )
    assert a != b


def test_result_hash_is_order_independent_over_dict_keys():
    assert compute_result_hash({"a": 1, "b": 2}) == compute_result_hash({"b": 2, "a": 1})


def test_no_warnings_passes_cleanly():
    outcome, obligations, hard_fail_reasons = evaluate_validation_policy([])
    assert outcome == Outcome.PASS
    assert obligations == []
    assert hard_fail_reasons == []


def test_warnings_become_explicit_obligations_not_silently_dropped():
    outcome, obligations, hard_fail_reasons = evaluate_validation_policy(["zero-cost model used"])
    assert outcome == Outcome.PASS_WITH_OBLIGATIONS
    assert obligations == ["zero-cost model used"]
    assert hard_fail_reasons == []


def test_backtest_engine_error_is_a_hard_fail_not_an_obligation():
    """F-04 — `hard_fail_reasons=()` 상수 결함의 핵심 회귀: 백테스트 재생
    도중 실제 로직 오류(PortfolioEngine 예외)가 나면 FAIL이어야 하고,
    hard_fail_reasons가 비어있으면 안 된다."""
    warning = "bar 3: PortfolioEngine 예외 — 이미 보유 포지션이 있는 상태에서 진입(BUY) 신호"
    outcome, obligations, hard_fail_reasons = evaluate_validation_policy([warning])
    assert outcome == Outcome.FAIL
    assert hard_fail_reasons == [warning]
    assert obligations == []


def test_dsr_and_pbo_threshold_breaches_are_hard_fail():
    outcome, obligations, hard_fail_reasons = evaluate_validation_policy(
        ["DSR 0.80 < min_dsr 0.95", "PBO 0.62 > max_pbo 0.5"]
    )
    assert outcome == Outcome.FAIL
    assert hard_fail_reasons == ["DSR 0.80 < min_dsr 0.95", "PBO 0.62 > max_pbo 0.5"]
    assert obligations == []


def test_data_leakage_warning_is_hard_fail():
    outcome, obligations, hard_fail_reasons = evaluate_validation_policy(
        ["look-ahead 데이터 누수 감지: bar 12"]
    )
    assert outcome == Outcome.FAIL
    assert hard_fail_reasons == ["look-ahead 데이터 누수 감지: bar 12"]
    assert obligations == []


def test_hard_fail_and_soft_warning_are_classified_independently():
    """하드페일 하나만 있어도 FAIL — 나머지 소프트 경고는 obligation으로
    같이 보존된다(하나가 다른 하나를 가리지 않는다)."""
    hard = "bar 1: PortfolioEngine 예외 — 로직 오류"
    soft = "zero-cost model used"
    outcome, obligations, hard_fail_reasons = evaluate_validation_policy([hard, soft])
    assert outcome == Outcome.FAIL
    assert hard_fail_reasons == [hard]
    assert obligations == [soft]


# -- L42 evaluate_bundle (§9 L42 / I6) -----------------------------------------


def _check_result(
    *,
    check_type: str = "backtest",
    outcome: Outcome = Outcome.PASS,
    hard_fail_reasons: list[str] | None = None,
    obligations: list[str] | None = None,
) -> CheckResult:
    return CheckResult(
        check_type=check_type,
        outcome=outcome,
        metrics={
            "period_start": NOW,
            "period_end": NOW,
            "periods_per_year": 252,
            "basis": "PAPER_SIM",
            "config_hash": "h",
        },
        hard_fail_reasons=hard_fail_reasons or [],
        obligations=obligations or [],
        result_hash="deadbeef",
        policy_version="vp-v1",
    )


def test_bundle_with_no_checks_passes_cleanly():
    outcome, obligations, hard_fail_reasons = evaluate_bundle([])
    assert outcome == Outcome.PASS
    assert obligations == []
    assert hard_fail_reasons == []


def test_bundle_obligations_from_multiple_checks_are_unioned():
    results = [
        _check_result(
            check_type="backtest",
            outcome=Outcome.PASS_WITH_OBLIGATIONS,
            obligations=["zero-cost model used"],
        ),
        _check_result(
            check_type="failure_conditions",
            outcome=Outcome.PASS_WITH_OBLIGATIONS,
            obligations=["REVALIDATE_IF_ROLLING_SHARPE_30D_LT_0"],
        ),
    ]
    outcome, obligations, hard_fail_reasons = evaluate_bundle(results)
    assert outcome == Outcome.PASS_WITH_OBLIGATIONS
    assert obligations == ["zero-cost model used", "REVALIDATE_IF_ROLLING_SHARPE_30D_LT_0"]
    assert hard_fail_reasons == []


def test_bundle_hard_fail_from_a_single_check_fails_the_whole_bundle():
    """negative 1 — I6: 6개 체크 중 하나만 hard fail이어도 번들 전체가
    FAIL이어야 한다(부분 PASS로 흐려지면 안 된다)."""
    results = [
        _check_result(check_type="point_in_time", outcome=Outcome.PASS),
        _check_result(
            check_type="backtest",
            outcome=Outcome.FAIL,
            hard_fail_reasons=["BACKTEST_LOOKAHEAD_VIOLATION"],
        ),
    ]
    outcome, obligations, hard_fail_reasons = evaluate_bundle(results)
    assert outcome == Outcome.FAIL
    assert hard_fail_reasons == ["BACKTEST_LOOKAHEAD_VIOLATION"]
    assert obligations == []


def test_bundle_hard_fail_does_not_swallow_other_checks_obligations():
    """negative 2 — hard fail이 다른 체크의 obligation을 가리거나 지우면
    안 된다(둘 다 결과에 그대로 보존)."""
    results = [
        _check_result(
            check_type="backtest",
            outcome=Outcome.PASS_WITH_OBLIGATIONS,
            obligations=["zero-cost model used"],
        ),
        _check_result(
            check_type="stress_capacity",
            outcome=Outcome.FAIL,
            hard_fail_reasons=["VALIDATION_SCENARIO_MISSING"],
        ),
    ]
    outcome, obligations, hard_fail_reasons = evaluate_bundle(results)
    assert outcome == Outcome.FAIL
    assert hard_fail_reasons == ["VALIDATION_SCENARIO_MISSING"]
    assert obligations == ["zero-cost model used"]


def test_bundle_rejects_unknown_hard_fail_code_at_construction():
    """negative 3 — I-07 닫힌 코드 집합 방어: 체크 저자가 즉석에서 지어낸
    코드는 `CheckResult` 생성 시점에 거부된다(evaluate_bundle에 도달하기
    전에 이미 막힘)."""
    with pytest.raises(ValidationError):
        _check_result(hard_fail_reasons=["MADE_UP_CODE_NOT_IN_SPEC"])


def test_bundle_ignores_mislabeled_per_check_outcome_field():
    """실패 주입 1건 — 체크 모듈에 버그가 있어 `outcome=PASS`인 채로
    `hard_fail_reasons`를 채워 넣는 (있어서는 안 될) 상태를 주입한다.
    `evaluate_bundle`은 각 체크의 `outcome` 필드를 신뢰하지 않고
    `hard_fail_reasons` 리스트만 근거로 삼으므로, 이 모순된 입력에서도
    번들은 fail-closed하게 FAIL을 반환해야 한다."""
    mislabeled = _check_result(
        check_type="point_in_time",
        outcome=Outcome.PASS,  # bug: should have been FAIL
        hard_fail_reasons=["INTEGRITY_FUTURE_DATA"],
    )
    outcome, obligations, hard_fail_reasons = evaluate_bundle([mislabeled])
    assert outcome == Outcome.FAIL
    assert hard_fail_reasons == ["INTEGRITY_FUTURE_DATA"]


def test_bundle_gate_red_repro_naive_outcome_trusting_aggregator_would_pass_incorrectly():
    """게이트 적색 재현 — "각 체크의 outcome 필드를 그대로 신뢰해 하나라도
    FAIL이면 FAIL"이라고 순진하게 짠 집계기는, 위 실패 주입 케이스처럼
    `outcome`이 잘못 매겨진 체크를 만나면 조용히 PASS를 내려버린다(적색).
    실제 `evaluate_bundle`은 outcome이 아니라 hard_fail_reasons 자체를
    근거로 삼아 같은 입력에서 올바르게 FAIL을 낸다(녹색)."""
    mislabeled = _check_result(
        check_type="point_in_time",
        outcome=Outcome.PASS,
        hard_fail_reasons=["INTEGRITY_FUTURE_DATA"],
    )

    def _naive_bundle_outcome(results: list[CheckResult]) -> Outcome:
        return Outcome.FAIL if any(r.outcome == Outcome.FAIL for r in results) else Outcome.PASS

    naive_outcome = _naive_bundle_outcome([mislabeled])
    assert naive_outcome == Outcome.PASS  # red: masks a real hard fail

    real_outcome, _obligations, _hard_fail_reasons = evaluate_bundle([mislabeled])
    assert real_outcome == Outcome.FAIL  # green: evaluate_bundle catches it


@pytest.mark.perf
def test_bundle_evaluation_throughput_within_local_budget():
    """성능 단언 1 — 순수 집계 함수라 사전거래 게이트급 예산(p99 5ms,
    ADR-2026-09-09-C)에 견줘도 훨씬 여유로워야 한다. 6개 체크 x 50 사이클
    분량(300건)을 반복 평가해 p95를 잰다."""
    results = [
        _check_result(
            check_type=f"check_{i % 6}",
            outcome=Outcome.PASS_WITH_OBLIGATIONS,
            obligations=[f"obligation_{i}"],
        )
        for i in range(300)
    ]
    samples: list[float] = []
    for _ in range(20):
        start = time.perf_counter()
        evaluate_bundle(results)
        samples.append(time.perf_counter() - start)
    samples.sort()
    p95_seconds = samples[int(len(samples) * 0.95)]
    assert p95_seconds < 0.005, f"p95={p95_seconds * 1000:.3f}ms exceeds 5ms budget"
