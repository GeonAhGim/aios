"""L4_risk_and_safety_v1.0.md#2.1, §9 R-14 — `check_exposure_limits` 판정 테스트.

DoD(task-1186): 6개 scope(TENANT/ACCOUNT/STRATEGY/SYMBOL/ASSET_CLASS/
PROVIDER) 각각 매칭·비매칭(특히 SYMBOL 한도가 다른 심볼에 미적용),
hard=DENY·soft=ESCALATE, 한도 경계값(=통과, 초과=거부), 입력 결손
fail-closed DENY.
"""
import time
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal, InvalidOperation
from uuid import uuid4

import pytest

from src.core.risk.decision import RiskOutcome
from src.core.risk.inputs import ActivityInputs, ExposureSnapshot
from src.core.risk.limits import ExposureLimit, LimitMetric, LimitScope, check_exposure_limits
from tests.unit.core.risk._rule_test_helpers import NOW, sample_inputs

_LOW_LIMIT = Decimal("1")  # sample_inputs()의 intent.notional(5000)보다 항상 작다


def _order_limit(
    *, scope: LimitScope, scope_ref: str, hard: bool = True, value: Decimal = _LOW_LIMIT
) -> ExposureLimit:
    return ExposureLimit(
        scope=scope,
        scope_ref=scope_ref,
        metric=LimitMetric.MAX_ORDER_NOTIONAL,
        limit_value=value,
        hard=hard,
        limit_id=uuid4(),
    )


def _run(inputs, *limits):
    return check_exposure_limits(inputs, tuple(limits))


# ---- scope 매칭 (6종 각각 매칭/비매칭) ----


def test_tenant_scope_matches_and_denies():
    tenant_id = uuid4()
    inputs = sample_inputs(tenant_id=tenant_id)
    result = _run(inputs, _order_limit(scope=LimitScope.TENANT, scope_ref=str(tenant_id)))
    assert result.outcome == RiskOutcome.DENY
    assert result.reason_code == "RISK_LIMIT_BREACH:TENANT:MAX_ORDER_NOTIONAL"


def test_tenant_scope_other_tenant_not_applied():
    inputs = sample_inputs(tenant_id=uuid4())
    result = _run(inputs, _order_limit(scope=LimitScope.TENANT, scope_ref=str(uuid4())))
    assert result.outcome == RiskOutcome.ALLOW


def test_account_scope_matches_and_denies():
    tenant_id = uuid4()
    inputs = sample_inputs(tenant_id=tenant_id)
    result = _run(inputs, _order_limit(scope=LimitScope.ACCOUNT, scope_ref=str(tenant_id)))
    assert result.outcome == RiskOutcome.DENY


def test_account_scope_other_account_not_applied():
    inputs = sample_inputs(tenant_id=uuid4())
    result = _run(inputs, _order_limit(scope=LimitScope.ACCOUNT, scope_ref=str(uuid4())))
    assert result.outcome == RiskOutcome.ALLOW


def test_strategy_scope_matches_and_denies():
    inputs = sample_inputs()
    result = _run(inputs, _order_limit(scope=LimitScope.STRATEGY, scope_ref="strat-1"))
    assert result.outcome == RiskOutcome.DENY


def test_strategy_scope_other_strategy_not_applied():
    inputs = sample_inputs()
    result = _run(inputs, _order_limit(scope=LimitScope.STRATEGY, scope_ref="strat-2"))
    assert result.outcome == RiskOutcome.ALLOW


def test_symbol_scope_matches_and_denies():
    inputs = sample_inputs()
    result = _run(inputs, _order_limit(scope=LimitScope.SYMBOL, scope_ref="BTC/USDT"))
    assert result.outcome == RiskOutcome.DENY


def test_symbol_scope_other_symbol_not_applied():
    """SYMBOL 한도가 다른 심볼 주문에 적용되지 않는다 — task-1186 DoD 핵심 케이스."""
    inputs = sample_inputs()
    result = _run(inputs, _order_limit(scope=LimitScope.SYMBOL, scope_ref="ETH/USDT"))
    assert result.outcome == RiskOutcome.ALLOW


def test_asset_class_scope_matches_and_denies():
    inputs = sample_inputs()
    result = _run(inputs, _order_limit(scope=LimitScope.ASSET_CLASS, scope_ref="CRYPTO_SPOT"))
    assert result.outcome == RiskOutcome.DENY


def test_asset_class_scope_other_class_not_applied():
    inputs = sample_inputs()
    result = _run(inputs, _order_limit(scope=LimitScope.ASSET_CLASS, scope_ref="EQUITY"))
    assert result.outcome == RiskOutcome.ALLOW


def test_provider_scope_matches_and_denies():
    inputs = sample_inputs(execution_ref="exec:1")
    result = _run(inputs, _order_limit(scope=LimitScope.PROVIDER, scope_ref="exec:1"))
    assert result.outcome == RiskOutcome.DENY


def test_provider_scope_other_provider_not_applied():
    inputs = sample_inputs(execution_ref="exec:1")
    result = _run(inputs, _order_limit(scope=LimitScope.PROVIDER, scope_ref="exec:2"))
    assert result.outcome == RiskOutcome.ALLOW


# ---- hard/soft ----


def test_hard_breach_denies():
    inputs = sample_inputs()
    result = _run(inputs, _order_limit(scope=LimitScope.SYMBOL, scope_ref="BTC/USDT", hard=True))
    assert result.outcome == RiskOutcome.DENY


def test_soft_breach_escalates():
    inputs = sample_inputs()
    result = _run(inputs, _order_limit(scope=LimitScope.SYMBOL, scope_ref="BTC/USDT", hard=False))
    assert result.outcome == RiskOutcome.ESCALATE
    assert result.reason_code == "RISK_LIMIT_BREACH:SYMBOL:MAX_ORDER_NOTIONAL"


def test_hard_breach_overrides_prior_soft_escalate():
    inputs = sample_inputs()
    soft = _order_limit(scope=LimitScope.SYMBOL, scope_ref="BTC/USDT", hard=False)
    hard = _order_limit(scope=LimitScope.STRATEGY, scope_ref="strat-1", hard=True)
    result = _run(inputs, soft, hard)
    assert result.outcome == RiskOutcome.DENY


# ---- 경계값 ----


def test_limit_value_equal_to_observed_allows():
    inputs = sample_inputs()  # intent.notional == 5000
    limit = ExposureLimit(
        scope=LimitScope.SYMBOL,
        scope_ref="BTC/USDT",
        metric=LimitMetric.MAX_ORDER_NOTIONAL,
        limit_value=Decimal("5000"),
        hard=True,
        limit_id=uuid4(),
    )
    result = _run(inputs, limit)
    assert result.outcome == RiskOutcome.ALLOW
    assert result.observed == Decimal("5000")


def test_limit_value_below_observed_denies():
    inputs = sample_inputs()
    limit = ExposureLimit(
        scope=LimitScope.SYMBOL,
        scope_ref="BTC/USDT",
        metric=LimitMetric.MAX_ORDER_NOTIONAL,
        limit_value=Decimal("4999.999999"),
        hard=True,
        limit_id=uuid4(),
    )
    result = _run(inputs, limit)
    assert result.outcome == RiskOutcome.DENY


# ---- 입력 결손 fail-closed(I2) ----


def test_missing_gross_leverage_denies():
    inputs = sample_inputs()  # exposure.gross_leverage 기본값 None
    limit = ExposureLimit(
        scope=LimitScope.TENANT,
        scope_ref=str(inputs.tenant_id),
        metric=LimitMetric.MAX_LEVERAGE,
        limit_value=Decimal("3"),
        hard=True,
        limit_id=uuid4(),
    )
    result = _run(inputs, limit)
    assert result.outcome == RiskOutcome.DENY
    assert result.reason_code == "RISK_INPUT_MISSING:exposure.gross_leverage"
    assert result.missing_fields == ("exposure.gross_leverage",)


def test_missing_trades_last_1h_denies():
    inputs = sample_inputs(activity=ActivityInputs(trades_last_1h=None))
    limit = ExposureLimit(
        scope=LimitScope.STRATEGY,
        scope_ref="strat-1",
        metric=LimitMetric.MAX_TRADES_PER_HOUR,
        limit_value=Decimal("10"),
        hard=True,
        limit_id=uuid4(),
    )
    result = _run(inputs, limit)
    assert result.outcome == RiskOutcome.DENY
    assert result.missing_fields == ("activity.trades_last_1h",)


def test_missing_gross_notional_pct_key_denies():
    inputs = sample_inputs()  # exposure.gross_notional 기본값 {}
    limit = ExposureLimit(
        scope=LimitScope.SYMBOL,
        scope_ref="BTC/USDT",
        metric=LimitMetric.GROSS_NOTIONAL_PCT,
        limit_value=Decimal("20"),
        hard=True,
        limit_id=uuid4(),
    )
    result = _run(inputs, limit)
    assert result.outcome == RiskOutcome.DENY
    assert result.missing_fields == ("exposure.gross_notional[SYMBOL:BTC/USDT]",)


# ---- 기타 ----


def test_no_limits_allows():
    inputs = sample_inputs()
    result = _run(inputs)
    assert result.outcome == RiskOutcome.ALLOW


def test_gross_notional_pct_breach_denies():
    inputs = sample_inputs(
        exposure=ExposureSnapshot(gross_notional={"SYMBOL:BTC/USDT": Decimal("25")}, as_of=NOW)
    )
    limit = ExposureLimit(
        scope=LimitScope.SYMBOL,
        scope_ref="BTC/USDT",
        metric=LimitMetric.GROSS_NOTIONAL_PCT,
        limit_value=Decimal("20"),
        hard=True,
        limit_id=uuid4(),
    )
    result = _run(inputs, limit)
    assert result.outcome == RiskOutcome.DENY
    assert result.observed == Decimal("25.000000")


# ---- 실패 주입 ----


def test_corrupted_nan_gross_leverage_raises_instead_of_silently_allowing():
    """실패 주입 — 상류 조립기가 부패한 `Decimal("NaN")`을 gross_leverage에
    흘려보내면(계산 오류 등), 기본 Decimal 컨텍스트는 NaN과의 순서 비교에서
    `InvalidOperation`을 던진다. `check_exposure_limits`가 이를 조용히
    삼켜 ALLOW로 위장해서는 안 된다 — 예외가 그대로 전파돼 evaluator(R-16)의
    `rule_error()`가 fail-closed DENY로 흡수하게 강제한다(I2).

    `ExposureSnapshot`은 평소 pydantic 검증이 non-finite Decimal을 막지만
    (`finite_number`), 신뢰 경계 안쪽(캐시 역직렬화, 다른 프로세스의 부분
    적으로 구성된 스냅샷 등)에서 검증을 우회해 들어온 값을 흉내내려면
    `model_construct`로 검증을 건너뛴 적대적 이중체가 필요하다."""
    corrupted_exposure = ExposureSnapshot.model_construct(
        gross_leverage=Decimal("NaN"), as_of=NOW
    )
    inputs = sample_inputs(exposure=corrupted_exposure)
    limit = ExposureLimit(
        scope=LimitScope.TENANT,
        scope_ref=str(inputs.tenant_id),
        metric=LimitMetric.MAX_LEVERAGE,
        limit_value=Decimal("3"),
        hard=True,
        limit_id=uuid4(),
    )
    with pytest.raises(InvalidOperation):
        _run(inputs, limit)


# ---- 성능 단언 ----


def test_bulk_evaluation_against_many_non_matching_limits_stays_within_perf_budget():
    """성능 단언 — R-16 evaluator가 매 결정마다 호출한다(§9). 실제 배포에서는
    한 주문에 수십 개의 scope 한도가 걸릴 수 있으므로, scope 미매칭 한도들을
    선형 스캔하는 비용이 병적으로 커지지 않아야 한다."""
    inputs = sample_inputs()
    non_matching = tuple(
        _order_limit(scope=LimitScope.SYMBOL, scope_ref=f"OTHER-{i}/USDT")
        for i in range(50)
    )

    start = time.perf_counter()
    for _ in range(500):
        result = check_exposure_limits(inputs, non_matching)
        assert result.outcome == RiskOutcome.ALLOW
    elapsed = time.perf_counter() - start

    assert elapsed < 2.0


# ---- 다중 인스턴스/리플레이 ----


def test_multiple_concurrent_workers_evaluate_independent_inputs_without_cross_contamination():
    """다중 인스턴스 증명 — 여러 게이트 워커 스레드가 각자 다른 테넌트의
    주문을 동시에 평가해도(순수 함수, 전역 가변 상태 없음) 서로의 결과를
    오염시키지 않는다. 각 워커는 자기 테넌트에 대해서만 DENY를 받아야 한다."""

    def _evaluate(i: int) -> tuple[str, RiskOutcome, str | None]:
        tenant_id = uuid4()
        worker_inputs = sample_inputs(tenant_id=tenant_id)
        limit = _order_limit(scope=LimitScope.TENANT, scope_ref=str(tenant_id))
        result = check_exposure_limits(worker_inputs, (limit,))
        return str(tenant_id), result.outcome, result.reason_code

    with ThreadPoolExecutor(max_workers=8) as pool:
        outcomes = list(pool.map(_evaluate, range(64)))

    assert len(outcomes) == 64
    for _tenant_id, outcome, reason_code in outcomes:
        assert outcome == RiskOutcome.DENY
        assert reason_code == "RISK_LIMIT_BREACH:TENANT:MAX_ORDER_NOTIONAL"


def test_check_exposure_limits_is_deterministic_for_replay():
    # 리플레이 증거 — R2: 같은 입력이면 같은 논리적 출력으로 재구성 가능해야 한다.
    inputs = sample_inputs()
    limits = (
        _order_limit(scope=LimitScope.SYMBOL, scope_ref="BTC/USDT", hard=False),
        _order_limit(scope=LimitScope.STRATEGY, scope_ref="strat-1", hard=True),
    )
    first = check_exposure_limits(inputs, limits)
    second = check_exposure_limits(inputs, limits)
    assert first == second
    assert first.model_dump() == second.model_dump()
