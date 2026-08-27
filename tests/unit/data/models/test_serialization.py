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
