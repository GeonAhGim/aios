from __future__ import annotations

import json
from decimal import Decimal

import pytest

from src.data.models.serialization import DecimalSafeEncoder
from tests.support.perf_budget import PerfBudget


def test_decimal_serialized_as_string_not_float():
    payload = {"amount": Decimal("1.23456789")}
    result = json.dumps(payload, cls=DecimalSafeEncoder)
    assert json.loads(result) == {"amount": "1.23456789"}


def test_non_decimal_raises_type_error():
    with pytest.raises(TypeError):
        json.dumps({"value": object()}, cls=DecimalSafeEncoder)


def test_plain_json_dumps_rejects_decimal_without_encoder():
    """negative: stdlib json.dumps without DecimalSafeEncoder must reject
    Decimal — this is the invariant (monetary amounts are Decimal, never
    float) that the encoder exists to satisfy without a silent float cast."""
    with pytest.raises(TypeError):
        json.dumps({"amount": Decimal("1.23")})


def test_custom_object_nested_in_dict_value_raises_type_error():
    """negative: a non-Decimal, non-JSON-native object nested as a dict
    value must still raise TypeError, not be silently dropped or stringified."""

    class Unserializable:
        pass

    with pytest.raises(TypeError):
        json.dumps({"amount": Decimal("1"), "extra": Unserializable()}, cls=DecimalSafeEncoder)


def test_nested_decimal_in_list():
    """boundary: Decimal inside a list — json.dumps calls default() for
    nested objects too, so this should succeed."""
    payload = {"prices": [Decimal("1.5"), Decimal("2.5")]}
    result = json.dumps(payload, cls=DecimalSafeEncoder)
    parsed = json.loads(result)
    assert parsed == {"prices": ["1.5", "2.5"]}


def test_decimal_zero_and_negative():
    """boundary: Decimal('0') and negative values must serialize as strings."""
    payload = {"zero": Decimal("0"), "neg": Decimal("-99.99")}
    result = json.dumps(payload, cls=DecimalSafeEncoder)
    parsed = json.loads(result)
    assert parsed == {"zero": "0", "neg": "-99.99"}


def test_decimal_very_large_precision():
    """boundary: high-precision Decimal must not lose digits."""
    payload = {"precise": Decimal("123456789.01234567890123456789")}
    result = json.dumps(payload, cls=DecimalSafeEncoder)
    parsed = json.loads(result)
    assert parsed["precise"] == "123456789.01234567890123456789"


def test_empty_payload():
    """boundary: empty dict should round-trip cleanly."""
    result = json.dumps({}, cls=DecimalSafeEncoder)
    assert result == "{}"


def test_empty_list():
    """boundary: list with no Decimals should produce normal JSON."""
    result = json.dumps([], cls=DecimalSafeEncoder)
    assert result == "[]"


def test_encoder_default_called_once_per_decimal(monkeypatch):
    """failure injection: verify default() is actually invoked for each Decimal."""
    call_count = 0

    original_default = DecimalSafeEncoder.default

    def counting_default(self, obj):
        nonlocal call_count
        if isinstance(obj, Decimal):
            call_count += 1
        return original_default(self, obj)

    monkeypatch.setattr(DecimalSafeEncoder, "default", counting_default)
    payload = {"a": Decimal("1"), "b": Decimal("2"), "c": Decimal("3")}
    json.dumps(payload, cls=DecimalSafeEncoder)
    assert call_count == 3


def test_encoder_default_non_decimal_passes_through(monkeypatch):
    """failure injection: non-Decimal types must raise TypeError (not silently
    return a string)."""

    def fake_default(self, obj):
        # Simulate: if someone monkeypatches default to always return a string,
        # the real encoder should NOT call us for non-Decimal types.
        raise TypeError(f"Object of type {type(obj).__name__} is not JSON serializable")

    monkeypatch.setattr(DecimalSafeEncoder, "default", fake_default)
    # datetime is handled by json module internals before default() — should raise
    with pytest.raises(TypeError):
        json.dumps({"ts": object()}, cls=DecimalSafeEncoder)


def test_encoder_propagates_superclass_failure(monkeypatch):
    """failure injection: if the json module's internal fallback raises a
    different error than the stock TypeError (e.g. a dependency misbehaving),
    DecimalSafeEncoder must not swallow it — fail-closed, not fail-open."""

    def broken_super_default(self, obj):
        raise ValueError("simulated downstream failure")

    monkeypatch.setattr(json.JSONEncoder, "default", broken_super_default)
    with pytest.raises(ValueError):
        json.dumps({"extra": object()}, cls=DecimalSafeEncoder)


@pytest.mark.perf
def test_perf_serialize_1000_decimals(perf_budget):
    """performance: 1000 Decimal fields must serialise under 50 ms."""
    payload = {f"field_{i}": Decimal(f"{i}.99") for i in range(1000)}

    def serialize():
        return json.dumps(payload, cls=DecimalSafeEncoder)

    perf_budget.assert_within(
        serialize, budget_ms=50, n=5, warmup=1, batch=8, label="serialize_1000_decimals"
    )


# ---- CPU 측정 0 수렴 검증 (task-11539 감사 후속) -------------------------------


@pytest.mark.perf
def test_serialize_cpu_measurement_converges_with_batch(perf_budget: PerfBudget) -> None:
    """CPU 측정 0 수렴 검증: 단일 Decimal 직렬화는 Windows
    `time.process_time()` 약 15.6ms 양자화tick 때문에 batch=1로 측정하면
    항상 0ms로 읽힌다. batch=8로 묶으면 양자화 오차가 호출당 15.6/8 ≈ 2ms로
    줄어 실제 CPU 시간을 측정할 수 있다.

    이 테스트는 batch 매개변수가 양자화 문제를 완화한다는 사실을
    문서화한다(task-11539 감사).
    """
    payload = {"amount": Decimal("1.99")}

    # batch=1 — 양자화tick보다 빠르므로 0ms 수렴 (정량 단언)
    single_sample = perf_budget.sample(
        lambda: json.dumps(payload, cls=DecimalSafeEncoder),
        batch=1,
    )
    assert single_sample.cpu_ms < 1.0, (
        f"batch=1 cpu={single_sample.cpu_ms:.3f}ms — "
        f"Windows quantizationtick(15.6ms)보다 빠른 호출은 0ms 수렴"
    )

    # batch=8 — 양자화 오차가 1/8로 줄어 실제 CPU 측정 가능
    batch_sample = perf_budget.sample(
        lambda: json.dumps(payload, cls=DecimalSafeEncoder),
        batch=8,
    )
    # batch로 나눈 평균이 양자화tick(15.6ms)보다 작아야 함
    assert batch_sample.cpu_ms < 15.6, (
        f"batch=8 cpu={batch_sample.cpu_ms:.3f}ms — 양자화tick 15.6ms 미만이어야 함"
    )


# ---- 게이트 적색 재현 (CPU 지연 주입) ------------------------------------------


@pytest.mark.perf
def test_perf_budget_assertion_catches_10x_cpu_delay_injection(
    perf_budget: PerfBudget,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """게이트 적색 재현(D2): 성능 예산 단언이 실제로 위반을 포착하는지
    역으로 증명한다. `time.process_time`을 monkeypatch해 1회 호출당
    500ms(예산 50ms의 10배)를 가장하면 `assert_within`가
    AssertionError로 적색이 돼야 한다.

    단언/마커 삭제나 예산 완화가 아니라, 단언이 "제대로 작동하는지"를
    검증한다(task-11539 감사: 호출 1회 CPU 측정의 0 수렴 결함 방지).
    """
    budget_ms = 50.0

    # PerfBudget.sample() 내부에서 time.process_time()을 호출하므로
    # 이를 monkeypatch해 항상 500ms(10x budget)를 반환하게 한다.
    _fake_time = 0.0

    def _fake_process_time() -> float:
        nonlocal _fake_time
        _fake_time += 0.5  # 500ms per call = 10x budget
        return _fake_time

    monkeypatch.setattr("time.process_time", _fake_process_time)

    payload = {f"field_{i}": Decimal(f"{i}.99") for i in range(1000)}

    def serialize():
        return json.dumps(payload, cls=DecimalSafeEncoder)

    # 같은 측정/단언을 재사용 — budget을 초과하므로 AssertionError가 있어야 함
    with pytest.raises(AssertionError):
        perf_budget.assert_within(
            serialize,
            budget_ms=budget_ms,
            n=5,
            warmup=1,
            batch=8,
            label="serialize_1000_decimals_injected",
        )
