"""L4_risk_and_safety_v1.0.md#2.1, §9 R-10~R-13 — DEEPEN(task-2816, DEPTH
audit docs/audit/DEPTH_R_EO.md#1177) D2->D3 증빙.

기존 test_var_es/test_correlation/test_trade_frequency/test_safety_state.py는
각 규칙의 함수를 격리 호출해 경계값·누락 필드 DENY(22 negative, 배치 중
최다)만 증명했다 — 배치 전체가 순수함수라는 이유로 실제 게이트
(evaluator.evaluate) 통과 재현, 성능 예산, 리플레이/다중 인스턴스 증거가
전무했다(D2 미달, 안전축 R/EO는 D3 하한). 이 파일이 그 부족분을 채운다:

1. 게이트 DENY 재현 — rule 함수가 아니라 evaluate() 전체 파이프라인이 4개
   규칙 각각의 위반으로 실제로 적색(DENY)이 됨을 증명한다.
2. 실패 주입 — 규칙 함수가 예외를 던져도 evaluate()가 fail-closed DENY로
   전환함을 4개 규칙 각각에 대해 증명한다(I2).
3. 성능 단언 — 4개 규칙 check() 반복 호출이 절대시간 예산 내에 있다.
4. 리플레이 결정론 + 동시 다중 인스턴스 — 같은 입력 재호출이 항상 같은
   결과를 내고(숨은 가변 상태 없음), 여러 스레드가 서로 다른 입력으로
   동시에 호출해도 서로 오염시키지 않는다(D3).
"""
from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from uuid import uuid4

import pytest

import src.core.risk.evaluator as evaluator_module
from src.core.risk.decision import GateKind, RiskOutcome
from src.core.risk.evaluator import _ORDER, evaluate
from src.core.risk.inputs import (
    ActivityInputs,
    EquityInputs,
    ExposureSnapshot,
    SafetyInputs,
    StatsInputs,
)
from src.core.risk.policy_bundle import BundleState, RiskRuleBundle
from src.core.risk.rules import correlation, safety_state, trade_frequency, var_es
from tests.unit.core.risk._rule_test_helpers import NOW, POLICY, sample_inputs

_TTL_SEC = 5.0


def _bundle(**overrides: object) -> RiskRuleBundle:
    fields: dict[str, object] = dict(
        id=uuid4(),
        version="policy-v1",
        rule_hash="a" * 64,
        engine_version="engine-test-1",
        policy_snapshot=POLICY.model_dump(mode="python"),
        state=BundleState.ACTIVE,
        created_by=uuid4(),
        approved_by=uuid4(),
    )
    fields.update(overrides)
    return RiskRuleBundle(**fields)  # type: ignore[arg-type]


def _safe_inputs(**overrides: object):
    """모든 규칙이 ALLOW하는 기준선(test_evaluator.py와 동일) — 개별 테스트가
    특정 필드만 깨뜨려 나머지 규칙은 계속 ALLOW로 남긴다."""
    base: dict[str, object] = dict(
        equity=EquityInputs(
            total_equity=Decimal("100000"),
            daily_pnl_pct=Decimal("0"),
            drawdown_pct=Decimal("0"),
            as_of=NOW,
        ),
        exposure=ExposureSnapshot(
            position_quantity=Decimal("0"),
            symbol_market_value=Decimal("0"),
            gross_leverage=Decimal("1"),
            as_of=NOW,
        ),
        stats=StatsInputs(
            var_pct=Decimal("1"),
            es_pct=Decimal("1"),
            var_method="parametric",
            bars_used=100,
            lookback_bars=100,
            correlated_exposure_pct=Decimal("1"),
            max_correlation=0.1,
            missing_pairs=(),
            as_of=NOW,
        ),
        activity=ActivityInputs(trades_last_1h=1, trades_avg_per_hour_24h=Decimal("10")),
        safety=SafetyInputs(
            circuit_breaker_level="normal",
            active_control_scopes=(),
            data_distrust_level="TRUSTED",
            distrust_sources_available=3,
            connection_fresh=True,
            execution_paused_by_safety=False,
            rule_bundle_active=True,
        ),
    )
    base.update(overrides)
    return sample_inputs(**base)


def _evaluate(inputs, **kwargs: object):
    bundle = kwargs.pop("bundle", None) or _bundle()
    kwargs.setdefault("gate_kind", GateKind.PRE_TRADE)
    kwargs.setdefault("trace_id", uuid4())
    kwargs.setdefault("now", NOW)
    kwargs.setdefault("ttl", _TTL_SEC)
    return evaluate(inputs, bundle, **kwargs)  # type: ignore[arg-type]


# ---- 게이트 DENY 재현(D2) — rule 함수 격리 호출이 아니라 evaluate() 전체 ----


@pytest.mark.parametrize(
    ("overrides", "expected_reason"),
    [
        (
            dict(
                stats=StatsInputs(
                    var_pct=Decimal("99"),
                    es_pct=Decimal("1"),
                    var_method="parametric",
                    bars_used=100,
                    correlated_exposure_pct=Decimal("1"),
                    max_correlation=0.1,
                    missing_pairs=(),
                    as_of=NOW,
                )
            ),
            "RISK_VAR_EXCEEDED",
        ),
        (
            dict(
                stats=StatsInputs(
                    var_pct=Decimal("1"),
                    es_pct=Decimal("1"),
                    var_method="parametric",
                    bars_used=100,
                    correlated_exposure_pct=Decimal("99"),
                    max_correlation=0.99,
                    missing_pairs=(),
                    as_of=NOW,
                )
            ),
            "RISK_CORRELATION_EXPOSURE_EXCEEDED",
        ),
        (
            dict(
                activity=ActivityInputs(
                    trades_last_1h=999, trades_avg_per_hour_24h=Decimal("10")
                )
            ),
            "RISK_TRADE_FREQUENCY_ANOMALY",
        ),
    ],
)
def test_gate_denies_end_to_end_for_each_rule(overrides: dict, expected_reason: str) -> None:
    """개별 rule 함수가 아니라 실제 게이트(evaluate())가 적색(DENY)이 됨을
    증명한다 — 배선이 실제로 통해 있다는 증거(I-10)."""
    decision = _evaluate(_safe_inputs(**overrides))
    assert decision.outcome == RiskOutcome.DENY
    assert expected_reason in decision.reason_codes


def test_gate_denies_end_to_end_for_safety_state_breach() -> None:
    """safety_state는 §4.2 평가 순서상 가장 먼저 평가된다 — 게이트가
    실제로 첫 규칙에서 적색(DENY)을 내고 이후 규칙을 평가하지 않는지까지
    증명한다."""
    decision = _evaluate(_safe_inputs(safety=SafetyInputs(circuit_breaker_level="halted")))
    assert decision.outcome == RiskOutcome.DENY
    assert "RISK_CIRCUIT_BREAKER_HALTED" in decision.reason_codes
    assert len(decision.rule_results) == 1
    assert decision.rule_results[0].rule_id == "safety_state"


def test_gate_denies_end_to_end_when_correlation_missing_pairs_present() -> None:
    """상관 페어 결손을 0.0(무상관)으로 암묵 치환해 통과시키던 레거시 결함이
    게이트 레벨에서도 재현되지 않음을 증명한다(R3, decision 각주)."""
    decision = _evaluate(
        _safe_inputs(
            stats=StatsInputs(
                var_pct=Decimal("1"),
                es_pct=Decimal("1"),
                var_method="parametric",
                bars_used=100,
                correlated_exposure_pct=Decimal("0.0"),
                max_correlation=0.1,
                missing_pairs=("BTC/USDT:ETH/USDT",),
                as_of=NOW,
            )
        )
    )
    assert decision.outcome == RiskOutcome.DENY
    assert "RISK_INPUT_MISSING:stats.missing_pairs" in decision.reason_codes


# ---- 실패 주입(D2) — 규칙 예외가 evaluate()에서 fail-closed DENY로 전환됨 ----


@pytest.mark.parametrize("rule_id", ["var_es", "correlation", "trade_frequency", "safety_state"])
def test_rule_exception_fails_closed_to_deny(monkeypatch: pytest.MonkeyPatch, rule_id: str) -> None:
    def _boom(_inputs: object, _policy: object) -> None:
        raise RuntimeError("boom")

    patched = tuple((rid, _boom if rid == rule_id else fn) for rid, fn in _ORDER)
    monkeypatch.setattr(evaluator_module, "_ORDER", patched)

    decision = _evaluate(_safe_inputs())
    assert decision.outcome == RiskOutcome.DENY
    failed = next(r for r in decision.rule_results if r.rule_id == rule_id)
    assert failed.reason_code == f"RISK_RULE_ERROR:{rule_id}"


# ---- 성능 단언(D2) ----

_PERF_SCENARIOS = [
    (
        var_es.var_es,
        sample_inputs(
            stats=StatsInputs(
                var_pct=Decimal("2.5"),
                es_pct=Decimal("4.0"),
                var_method="cornish_fisher",
                bars_used=250,
                as_of=NOW,
            )
        ),
    ),
    (
        correlation.correlation,
        sample_inputs(
            stats=StatsInputs(
                correlated_exposure_pct=Decimal("10.0"),
                max_correlation=0.5,
                missing_pairs=(),
                as_of=NOW,
            )
        ),
    ),
    (
        trade_frequency.trade_frequency,
        sample_inputs(
            activity=ActivityInputs(trades_last_1h=1, trades_avg_per_hour_24h=Decimal("2"))
        ),
    ),
    (
        safety_state.safety_state,
        sample_inputs(
            safety=SafetyInputs(
                circuit_breaker_level="normal",
                active_control_scopes=(),
                data_distrust_level="NORMAL",
                execution_paused_by_safety=False,
                connection_fresh=True,
            )
        ),
    ),
]


@pytest.mark.perf
def test_rule_checks_meet_latency_budget() -> None:
    """4개 규칙 함수는 순수 Decimal/문자열 비교라 매우 빨라야 한다 — 절대시간
    예산은 넉넉히 잡아(느린 CI 머신 대비) 회귀(예: 반복 파싱·O(n^2))만 잡는다."""
    iterations = 2000
    budget_sec = 2.0  # 실측 로컬 <0.1s
    start = time.perf_counter()
    for _ in range(iterations):
        for fn, inputs in _PERF_SCENARIOS:
            fn(inputs, POLICY)
    elapsed = time.perf_counter() - start
    total_calls = iterations * len(_PERF_SCENARIOS)
    print(
        f"[R-10..R-13] {total_calls} rule calls in {elapsed:.3f}s "
        f"({elapsed / total_calls * 1e6:.2f} us/call, budget<{budget_sec}s)"
    )
    assert elapsed < budget_sec, (
        f"4개 risk 규칙 {total_calls}회가 예산({budget_sec}s)을 넘었습니다 "
        f"({elapsed:.3f}s) — Decimal 연산 비용이 늘었는지 확인하세요."
    )


# ---- 리플레이 결정론 + 동시 다중 인스턴스(D3) ----


def test_replay_is_deterministic_for_each_rule() -> None:
    """같은 입력으로 두 번 호출해도 완전히 동일한 RuleResult가 나온다 — 숨은
    시계·난수·전역 가변 상태가 없다는 재생(replay) 안전성 증거(R2)."""
    for fn, inputs in _PERF_SCENARIOS:
        first = fn(inputs, POLICY)
        second = fn(inputs, POLICY)
        assert first == second


def test_concurrent_instances_do_not_cross_contaminate() -> None:
    """서로 다른 입력을 가진 다중 워커(스레드, 동시 다중 인스턴스 시뮬레이션)가
    같은 규칙 모듈(var_es)을 동시에 호출해도 서로의 결과를 오염시키지 않는다
    — 모듈 레벨 가변 상태가 없다는 동시성 증거(D3 다중 인스턴스 증명)."""

    def _expected_outcome(var_pct: int) -> RiskOutcome:
        # policy.var.max_pct=5.0 — 그 이하는 ALLOW, 초과는 DENY.
        return RiskOutcome.ALLOW if var_pct <= 5 else RiskOutcome.DENY

    def _run(i: int) -> tuple[int, RiskOutcome]:
        inputs = sample_inputs(
            stats=StatsInputs(
                var_pct=Decimal(str(i)),
                es_pct=Decimal("1"),
                var_method="parametric",
                bars_used=100,
                as_of=NOW,
            )
        )
        result = var_es.var_es(inputs, POLICY)
        return i, result.outcome

    indices = list(range(1, 11)) * 30  # 300회 동시 호출, 서로 다른 입력이 반복 교차
    with ThreadPoolExecutor(max_workers=16) as pool:
        results = list(pool.map(_run, indices))

    assert len(results) == len(indices)
    for i, outcome in results:
        assert outcome == _expected_outcome(i), (
            f"i={i} 스레드가 다른 입력의 결과와 섞였다: {outcome}"
        )
