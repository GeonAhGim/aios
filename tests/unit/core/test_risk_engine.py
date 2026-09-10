import time
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from uuid import UUID, uuid4

import pytest

from src.core.loader.risk_policy_loader import load_risk_policy
from src.core.portfolio.models import AllocationDecision
from src.core.risk.decision import RiskDecision, RiskOutcome
from src.core.risk.engine import RiskEngine
from src.core.risk.inputs import RiskInputs
from src.core.risk.models import RiskCheckResult


@pytest.fixture
def policy():
    return load_risk_policy()


@pytest.fixture
def allocation():
    return AllocationDecision(
        symbol="BTC/USDT",
        strategy_id="strat-1",
        approved_quantity=Decimal("0.02"),
        capital_pct=Decimal("10"),
    )


def _valid_account_state(**overrides):
    base = {
        "daily_pnl_pct": Decimal("-1"),
        "drawdown_pct": Decimal("2"),
        "position_quantity": Decimal("0"),
        "total_equity": Decimal("10000"),
        "certified_badge": False,
        "allocated_capital": Decimal("1000"),
        "available_balance": Decimal("10000"),
        "var_pct": Decimal("1"),
        "correlated_exposure_pct": Decimal("5"),
        "recent_trade_count_1h": 1,
        "avg_trade_count_24h": 5.0,
        "circuit_breaker_level": "normal",
        "execution_paused_by_safety": False,
        "leverage": Decimal("1"),
    }
    base.update(overrides)
    return base


def _bridged_inputs(policy, allocation, **account_overrides) -> RiskInputs:
    """`check_decision()`(R-17 신규 공개 계약)은 `RiskInputs`를 직접 받는다
    — 기존 legacy dict 픽스처를 그대로 `RiskEngine._bridge_legacy_inputs`로
    변환만 해서 재사용한다(변환 로직 자체는 `test_all_indicators_pass_approves`
    등 기존 `check()` 테스트가 이미 검증)."""
    engine = RiskEngine(policy)
    return engine._bridge_legacy_inputs(allocation, _valid_account_state(**account_overrides))


def test_all_indicators_pass_approves(policy, allocation):
    engine = RiskEngine(policy)

    result = engine.check(allocation, _valid_account_state())

    assert result.approved is True
    assert result.rejection_reason is None
    assert result.checked_rules == [
        "daily_loss",
        "max_drawdown",
        "leverage",
        "position_concentration",
        "strategy_allocation",
        "var",
        "correlation_risk",
        "trade_frequency",
        "safety_state",
    ]


def test_daily_loss_rejects_and_short_circuits(policy, allocation):
    engine = RiskEngine(policy)

    result = engine.check(allocation, _valid_account_state(daily_pnl_pct=Decimal("-5")))

    assert result.approved is False
    assert result.rejection_reason == "daily_loss_halt_exceeded"
    assert result.checked_rules == ["daily_loss"]


def test_max_drawdown_rejects(policy, allocation):
    engine = RiskEngine(policy)

    result = engine.check(allocation, _valid_account_state(drawdown_pct=Decimal("15")))

    assert result.approved is False
    assert result.rejection_reason == "max_drawdown_hard_stop_exceeded"


def test_position_concentration_rejects_on_entry(policy, allocation):
    engine = RiskEngine(policy)
    over_limit_allocation = allocation.model_copy(update={"capital_pct": Decimal("25")})

    result = engine.check(
        over_limit_allocation, _valid_account_state(position_quantity=Decimal("0"))
    )

    assert result.approved is False
    assert result.rejection_reason == "position_concentration_exceeded"


def test_position_concentration_skipped_on_exit(policy, allocation):
    engine = RiskEngine(policy)
    exit_allocation = allocation.model_copy(update={"capital_pct": Decimal("25")})

    result = engine.check(
        exit_allocation, _valid_account_state(position_quantity=Decimal("0.1"))
    )

    assert result.approved is True


def test_strategy_allocation_rejects_when_over_cap(policy, allocation):
    engine = RiskEngine(policy)

    result = engine.check(
        allocation,
        _valid_account_state(
            certified_badge=False,
            allocated_capital=Decimal("2000"),
            available_balance=Decimal("10000"),
        ),
    )

    assert result.approved is False
    assert result.rejection_reason == "strategy_allocation_exceeded"


def test_var_rejects_when_exceeded(policy, allocation):
    engine = RiskEngine(policy)

    result = engine.check(allocation, _valid_account_state(var_pct=Decimal("10")))

    assert result.approved is False
    assert result.rejection_reason == "var_exceeded"


def test_correlation_risk_rejects_when_exceeded(policy, allocation):
    engine = RiskEngine(policy)

    result = engine.check(allocation, _valid_account_state(correlated_exposure_pct=Decimal("40")))

    assert result.approved is False
    assert result.rejection_reason == "correlation_risk_exceeded"


def test_leverage_rejects_when_exceeded(policy, allocation):
    """사용자 승인(2026-09-02) FROZEN 존 수정 회귀 테스트 — 이전엔 leverage가
    `checked_rules`에만 이름을 올릴 뿐 실제로는 아무것도 비교하지 않았다."""
    engine = RiskEngine(policy)

    result = engine.check(allocation, _valid_account_state(leverage=Decimal("5")))

    assert result.approved is False
    assert result.rejection_reason == "leverage_exceeded"


def test_leverage_at_policy_default_max_is_allowed(policy, allocation):
    engine = RiskEngine(policy)

    result = engine.check(
        allocation, _valid_account_state(leverage=Decimal(str(policy.leverage.default_max)))
    )

    assert result.approved is True


def test_trade_frequency_rejects_anomaly(policy, allocation):
    engine = RiskEngine(policy)

    result = engine.check(
        allocation,
        _valid_account_state(recent_trade_count_1h=100, avg_trade_count_24h=5.0),
    )

    assert result.approved is False
    assert result.rejection_reason == "trade_frequency_anomaly"


def test_trade_frequency_zero_baseline_does_not_reject(policy, allocation):
    engine = RiskEngine(policy)

    result = engine.check(
        allocation,
        _valid_account_state(recent_trade_count_1h=3, avg_trade_count_24h=0.0),
    )

    assert result.approved is True


def test_safety_state_rejects_when_circuit_breaker_restricted(policy, allocation):
    engine = RiskEngine(policy)

    result = engine.check(
        allocation, _valid_account_state(circuit_breaker_level="restricted")
    )

    assert result.approved is False
    assert result.rejection_reason == "safety_state_blocked"


def test_safety_state_rejects_when_execution_paused_by_safety(policy, allocation):
    engine = RiskEngine(policy)

    result = engine.check(
        allocation, _valid_account_state(execution_paused_by_safety=True)
    )

    assert result.approved is False
    assert result.rejection_reason == "safety_state_blocked"


def test_safety_state_checked_last_even_if_it_would_reject(policy, allocation):
    """다른 7개 지표를 전부 통과해도 Circuit Breaker RESTRICTED 이상이면
    거부되는지 확인 — FD-8.3 완료조건."""
    engine = RiskEngine(policy)

    result = engine.check(
        allocation, _valid_account_state(circuit_breaker_level="emergency")
    )

    assert result.approved is False
    assert result.checked_rules[-1] == "safety_state"


@pytest.mark.parametrize(
    "missing_key",
    [
        "daily_pnl_pct",
        "drawdown_pct",
        "total_equity",
        "var_pct",
        "correlated_exposure_pct",
        "recent_trade_count_1h",
        "circuit_breaker_level",
        "leverage",
    ],
)
def test_missing_data_rejects_not_approves(policy, allocation, missing_key):
    """판단 불가를 승인으로 취급하지 않는다 — Master Authority 핵심 원칙."""
    engine = RiskEngine(policy)
    state = _valid_account_state()
    state[missing_key] = None

    result = engine.check(allocation, state)

    assert result.approved is False
    assert result.rejection_reason is not None and result.rejection_reason.endswith(
        "data_unavailable"
    )


# ---- check_decision() 신규 공개 계약(R-17) — decision_id·RiskDecision 자체 검증 ----
# 위 테스트들은 전부 legacy `check()` 경로만 거쳐 왔다. `check_decision()`이 반환하는
# `RiskDecision`(decision_id·rule_results·is_actionable)과 `RiskCheckResult.decision_id`
# 배선은 여태 한 번도 직접 단언된 적이 없었다(DEPTH 감사 D2 미달 사유).


def test_check_decision_returns_risk_decision_with_deny_and_decision_id(policy, allocation):
    """부정 1/3 — `check_decision()`이 legacy `check()`와 별개로 그 자체로
    `RiskDecision`(decision_id 포함)을 돌려주는지 직접 확인한다."""
    engine = RiskEngine(policy)
    # halt_pct=5.0(fixture policy) — 정확히 5는 경계라 ESCALATE이므로 확실히
    # 초과하는 -6을 쓴다.
    inputs = _bridged_inputs(policy, allocation, daily_pnl_pct=Decimal("-6"))

    decision = engine.check_decision(inputs)

    assert isinstance(decision, RiskDecision)
    assert decision.outcome == RiskOutcome.DENY
    assert isinstance(decision.decision_id, UUID)
    assert any(code.startswith("RISK_DAILY_LOSS_HALT") for code in decision.reason_codes)


def test_check_decision_denies_on_missing_equity_input_fail_closed(policy, allocation):
    """부정 2/3 — `check()` 래퍼가 아니라 `check_decision()` 자체가 판단 불가를
    승인으로 새지 않는지(I2) 직접 확인한다."""
    engine = RiskEngine(policy)
    inputs = _bridged_inputs(policy, allocation)
    inputs = inputs.model_copy(
        update={"equity": inputs.equity.model_copy(update={"total_equity": None})}
    )

    decision = engine.check_decision(inputs)

    assert decision.outcome == RiskOutcome.DENY
    assert any(r.missing_fields for r in decision.rule_results)


def test_check_decision_denies_when_safety_state_restricted(policy, allocation):
    """부정 3/3 — safety_state는 R-16 평가 순서상 가장 먼저 평가된다(evaluator.py
    `_ORDER`). `check_decision()`으로 직접 확인 — 단 하나의 규칙만 평가되고 즉시
    단락된다."""
    engine = RiskEngine(policy)
    inputs = _bridged_inputs(policy, allocation, circuit_breaker_level="restricted")

    decision = engine.check_decision(inputs)

    assert decision.outcome == RiskOutcome.DENY
    assert [r.rule_id for r in decision.rule_results] == ["safety_state"]


def test_check_decision_gate_turns_red_when_only_last_rule_fails(policy, allocation):
    """게이트 적색 재현 — trade_frequency는 R-16 평가 순서상 마지막이다. 다른 9개
    규칙을 전부 통과해도(단락 없이 전부 평가) 이 하나로 게이트가 적색(DENY)이
    되고, `is_actionable()`이 실행 불가를 정확히 신호하는지 확인한다(execution_loop
    tick_risk_phase.py t5가 그대로 의존하는 계약)."""
    engine = RiskEngine(policy)
    inputs = _bridged_inputs(
        policy, allocation, recent_trade_count_1h=100, avg_trade_count_24h=5.0
    )

    decision = engine.check_decision(inputs)

    assert decision.outcome == RiskOutcome.DENY
    assert len(decision.rule_results) == 10
    assert decision.rule_results[-1].rule_id == "trade_frequency"
    assert decision.is_actionable(inputs.as_of) is False


def test_check_decision_corrupted_nan_input_fails_closed_not_silently_allowed(policy, allocation):
    """실패 주입 — 상류(캐시 역직렬화 등)에서 검증을 우회해 흘러든 손상된
    `Decimal("NaN")`(신뢰 경계 안쪽, `model_copy`는 재검증하지 않는다)이 규칙
    평가 중 `InvalidOperation`을 던져도 `check_decision()` 밖으로 예외가 새거나
    조용히 ALLOW로 위장되지 않고, evaluator의 `rule_error()`가 fail-closed DENY로
    흡수하는지 확인한다(I2)."""
    engine = RiskEngine(policy)
    inputs = _bridged_inputs(policy, allocation)
    corrupted = inputs.model_copy(
        update={"equity": inputs.equity.model_copy(update={"daily_pnl_pct": Decimal("NaN")})}
    )

    decision = engine.check_decision(corrupted)

    assert decision.outcome == RiskOutcome.DENY
    assert "RISK_RULE_ERROR:daily_loss" in decision.reason_codes


def test_check_decision_repeated_calls_stay_within_perf_budget(policy, allocation):
    """성능 단언 — `check_decision()`은 매 tick마다 호출된다(R-32 t3). 매 호출마다
    `RiskRuleBundle` 조립·`compute_rule_hash`(정책 전체 canonical_json+sha256)가
    재계산되므로, 그 오버헤드가 병적으로 커지지 않아야 한다."""
    engine = RiskEngine(policy)
    inputs = _bridged_inputs(policy, allocation)

    start = time.perf_counter()
    for _ in range(200):
        decision = engine.check_decision(inputs)
        assert decision.outcome == RiskOutcome.ALLOW
    elapsed = time.perf_counter() - start

    assert elapsed < 5.0


def test_check_decision_multiple_engine_instances_agree_on_decision(
    policy, allocation, monkeypatch
):
    """다중 인스턴스 증명 — 별도 워커 프로세스를 흉내내 두 개의 독립된
    `RiskEngine(policy)` 인스턴스가(각자 자기 번들을 조립) 동일 입력에 동일
    trace_id로 평가하면 완전히 같은 `decision_id`·`rule_hash`·outcome을 내야
    한다(R2 재생 가능성) — 인스턴스별로 번들 조립이 갈라지면 감사 추적이
    엇갈린다."""
    fixed_trace = uuid4()
    monkeypatch.setattr("src.core.risk.engine.uuid4", lambda: fixed_trace)
    inputs = _bridged_inputs(policy, allocation, daily_pnl_pct=Decimal("-6"))

    engine_a = RiskEngine(policy)
    engine_b = RiskEngine(policy)
    decision_a = engine_a.check_decision(inputs)
    decision_b = engine_b.check_decision(inputs)

    assert decision_a.decision_id == decision_b.decision_id
    assert decision_a.rule_hash == decision_b.rule_hash
    # latency_us는 실측 소요시간이라 인스턴스마다 다를 수 있다 — 그 외 전부 일치.
    assert decision_a.model_dump(exclude={"latency_us"}) == decision_b.model_dump(
        exclude={"latency_us"}
    )


def test_check_decision_concurrent_calls_do_not_cross_contaminate_results(policy, allocation):
    """다중 인스턴스 증명 — 여러 워커 스레드가 하나의 공유 `RiskEngine`
    인스턴스로 동시에 서로 다른 입력을 평가해도(순수 함수·불변 정책, 스레드
    간 공유 가변 상태 없음) 결과가 뒤섞이지 않는다. 각 워커는 자기가 넣은
    입력에 대응하는 outcome만 받아야 한다."""
    engine = RiskEngine(policy)

    def _evaluate(i: int) -> tuple[bool, RiskOutcome]:
        breach = i % 2 == 0
        inputs = _bridged_inputs(
            policy, allocation, daily_pnl_pct=Decimal("-6") if breach else Decimal("-1")
        )
        decision = engine.check_decision(inputs)
        return breach, decision.outcome

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(_evaluate, range(64)))

    assert len(results) == 64
    for breach, outcome in results:
        assert outcome == (RiskOutcome.DENY if breach else RiskOutcome.ALLOW)


def test_check_legacy_facade_propagates_decision_id_from_check_decision(
    policy, allocation, monkeypatch
):
    """배선 검증 — legacy `check()`가 내부적으로 `check_decision()`을 호출해
    얻은 `RiskDecision.decision_id`를 실제로 `RiskCheckResult.decision_id`에
    실어 나르는지 확인한다. `RiskCheckResult.decision_id`는 기본값이 `None`이라
    (`models.py`), 이 배선이 빠지면 이 필드는 조용히 항상 `None`으로 남는다 —
    바로 이 leaf가 채우려는 D2 공백."""
    fixed_trace = uuid4()
    monkeypatch.setattr("src.core.risk.engine.uuid4", lambda: fixed_trace)
    engine = RiskEngine(policy)
    inputs = _bridged_inputs(policy, allocation, daily_pnl_pct=Decimal("-5"))
    monkeypatch.setattr(engine, "_bridge_legacy_inputs", lambda *a, **k: inputs)

    result = engine.check(allocation, _valid_account_state(daily_pnl_pct=Decimal("-5")))
    decision = engine.check_decision(inputs)

    assert result.decision_id is not None
    assert result.decision_id == decision.decision_id


def test_risk_check_result_decision_id_defaults_to_none_for_backward_compat():
    """모델 계약 — `decision_id`는 옵션 필드(기본값 `None`)라 인자 없이 생성하는
    기존 호출부를 깨지 않는다(models.py docstring 명시 계약)."""
    result = RiskCheckResult()

    assert result.decision_id is None
