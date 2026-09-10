"""L4_risk_and_safety_v1.0.md#2.1, §9 R-04 — rules/base.py 계약 테스트."""
import time
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal, InvalidOperation

import pytest
from pydantic import ValidationError

from src.core.risk.decision import RiskOutcome, RuleResult
from src.core.risk.rules.base import Rule, missing, pct, rule_error


def test_missing_denies_and_fills_missing_fields():
    result = missing("daily_loss", "equity.daily_pnl_pct")

    assert result.outcome == RiskOutcome.DENY
    assert result.missing_fields == ("equity.daily_pnl_pct",)
    assert result.reason_code == "RISK_INPUT_MISSING:equity.daily_pnl_pct"
    assert result.unit == "pct"


def test_missing_accepts_explicit_unit():
    result = missing("leverage", "exposure.gross_leverage", unit="x")
    assert result.unit == "x"


def test_ruleresult_forbids_missing_fields_without_deny():
    # I2 — 결손 필드가 있으면서 ALLOW로 새는 것은 금지된다(fail-closed).
    with pytest.raises(ValidationError):
        RuleResult(
            rule_id="daily_loss",
            outcome=RiskOutcome.ALLOW,
            unit="pct",
            missing_fields=("equity.daily_pnl_pct",),
        )


def test_rule_error_denies_with_risk_rule_error_reason_code():
    def _broken_rule() -> RuleResult:
        raise ZeroDivisionError("boom")

    try:
        _broken_rule()
        result = None
    except ZeroDivisionError:
        # evaluator(R-16)가 규칙 예외를 잡아 이 헬퍼로 DENY를 만든다(I2).
        result = rule_error("daily_loss")

    assert result is not None
    assert result.outcome == RiskOutcome.DENY
    assert result.reason_code == "RISK_RULE_ERROR:daily_loss"
    assert result.missing_fields == ()


def test_pct_quantizes_to_six_decimals():
    assert pct(Decimal("12.3")) == Decimal("12.300000")
    assert pct(Decimal("1.123456789")) == Decimal("1.123457")


def test_pct_rounds_half_to_even_at_precision_boundary():
    assert pct(Decimal("1.0000005")) == Decimal("1.000000")
    assert pct(Decimal("1.0000015")) == Decimal("1.000002")


def test_rule_protocol_matches_plain_function_signature():
    def _allow_all(inputs: object, policy: object) -> RuleResult:
        return RuleResult(rule_id="noop", outcome=RiskOutcome.ALLOW, unit="pct")

    conforming: Rule = _allow_all  # mypy가 시그니처 불일치를 잡아낸다
    result = conforming(object(), object())  # type: ignore[arg-type]
    assert result.outcome == RiskOutcome.ALLOW


def test_missing_rejects_invalid_unit():
    # negative — RuleUnit 밖의 값이 조용히 통과하면 하류(§8 단위 표)에서
    # unit 불일치가 새어나간다.
    with pytest.raises(ValidationError):
        missing("daily_loss", "equity.daily_pnl_pct", unit="bogus")  # type: ignore[arg-type]


def test_rule_error_rejects_invalid_unit():
    # negative — missing()과 동일한 방어를 rule_error()에도 요구한다.
    with pytest.raises(ValidationError):
        rule_error("daily_loss", unit="bogus")  # type: ignore[arg-type]


def test_pct_rejects_infinity():
    # negative — 적대적/손상 입력(Infinity)은 Decimal 컨텍스트가 기본으로
    # InvalidOperation을 던진다(quantize 불가) — 조용히 통과하지 않는다.
    with pytest.raises(InvalidOperation):
        pct(Decimal("Infinity"))
    with pytest.raises(InvalidOperation):
        pct(Decimal("-Infinity"))


def test_pct_raises_on_non_decimal_input():
    """negative + 실패 주입 — 상류 조립 코드가 실수로 `float`/`str`을
    Decimal 대신 넘기면(직렬화 왕복 버그 등), pct()는 조용히 형변환하지
    않고 즉시 실패해야 한다(I2 — 판단 불가를 성공으로 위장하지 않는다)."""
    with pytest.raises(AttributeError):
        pct("12.3")  # type: ignore[arg-type]


def test_pct_bulk_calls_stay_within_perf_budget():
    # 성능 단언 — R-05~R-13이 매 평가마다 여러 번 호출한다(§9). 숨은 I/O나
    # O(n^2) 회귀가 생기면 게이트 지연(latency_us)에 그대로 누적된다.
    values = [Decimal("1.123456789") + Decimal(i) for i in range(10_000)]

    start = time.perf_counter()
    for v in values:
        pct(v)
    elapsed = time.perf_counter() - start

    assert elapsed < 1.0


def test_gate_stays_red_when_a_rule_raises_mid_evaluation():
    """게이트 적색 재현 — evaluator(R-16)의 루프를 축약 재현한다: 규칙 중
    하나가 예외를 던져도 rule_error()가 흡수해 전체 게이트가 여전히
    DENY(적색)로 유지된다."""

    def _ok_rule(inputs: object, policy: object) -> RuleResult:
        return RuleResult(rule_id="ok", outcome=RiskOutcome.ALLOW, unit="pct")

    def _broken_rule(inputs: object, policy: object) -> RuleResult:
        raise RuntimeError("adapter timeout")

    rules: list[Rule] = [_ok_rule, _broken_rule]
    results: list[RuleResult] = []
    for rule in rules:
        try:
            result = rule(object(), object())  # type: ignore[arg-type]
        except Exception:  # noqa: BLE001 — I2: evaluator(R-16)와 동일하게 fail-closed
            result = rule_error("broken")
        results.append(result)

    gate_outcome = (
        RiskOutcome.DENY
        if any(r.outcome == RiskOutcome.DENY for r in results)
        else RiskOutcome.ALLOW
    )
    assert gate_outcome == RiskOutcome.DENY
    assert results[-1].reason_code == "RISK_RULE_ERROR:broken"


def test_missing_and_pct_are_thread_safe_across_concurrent_instances():
    """다중 인스턴스 증명 — 여러 게이트 워커가 동시에 같은 순수 함수를
    호출해도(전역 가변 상태 없음) 서로의 결과를 오염시키지 않는다."""

    def _call(i: int) -> tuple[RuleResult, Decimal]:
        result = missing(f"rule-{i}", f"field-{i}")
        quantized = pct(Decimal(i) + Decimal("0.1234565"))
        return result, quantized

    with ThreadPoolExecutor(max_workers=16) as pool:
        outcomes = list(pool.map(_call, range(200)))

    for i, (result, quantized) in enumerate(outcomes):
        assert result.reason_code == f"RISK_INPUT_MISSING:field-{i}"
        assert result.missing_fields == (f"field-{i}",)
        assert quantized == pct(Decimal(i) + Decimal("0.1234565"))


def test_missing_is_deterministic_for_replay():
    # 리플레이 증거 — R2: 같은 입력이면 같은 논리적 출력으로 재구성 가능해야 한다.
    first = missing("daily_loss", "equity.daily_pnl_pct")
    second = missing("daily_loss", "equity.daily_pnl_pct")
    assert first == second
    assert first.model_dump() == second.model_dump()
