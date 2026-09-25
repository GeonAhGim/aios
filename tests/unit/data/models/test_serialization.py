import json
from decimal import Decimal

import pytest

from src.data.models.serialization import DecimalSafeEncoder


def test_decimal_serialized_as_string_not_float():
    payload = {"amount": Decimal("1.23456789")}
    result = json.dumps(payload, cls=DecimalSafeEncoder)
    assert json.loads(result) == {"amount": "1.23456789"}


def test_non_decimal_raises_type_error():
    with pytest.raises(TypeError):
        json.dumps({"value": object()}, cls=DecimalSafeEncoder)


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


def test_perf_serialize_1000_decimals():
    """performance: 1000 Decimal fields must serialise under 50 ms."""
    import time

    payload = {f"field_{i}": Decimal(f"{i}.99") for i in range(1000)}
    start = time.perf_counter()
    json.dumps(payload, cls=DecimalSafeEncoder)
    elapsed_ms = (time.perf_counter() - start) * 1000
    assert elapsed_ms < 50, f"Serialization took {elapsed_ms:.1f}ms (budget: 50ms)"
