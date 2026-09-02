"""L4_risk_and_safety_v1.0.md#9 R-01 — hashing.py canonical JSON 결정론 테스트."""
from datetime import datetime, timezone
from decimal import Decimal
from uuid import UUID

import pytest

from src.core.risk.hashing import canonical_json, sha256_hex


def test_key_order_does_not_change_hash():
    a = {"b": 1, "a": 2}
    b = {"a": 2, "b": 1}
    assert sha256_hex(canonical_json(a)) == sha256_hex(canonical_json(b))


def test_decimal_trailing_zero_normalized_to_same_hash():
    a = {"x": Decimal("1.0")}
    b = {"x": Decimal("1.00")}
    assert canonical_json(a) == canonical_json(b)


def test_decimal_zero_variants_normalized_to_same_hash():
    assert canonical_json({"x": Decimal("0")}) == canonical_json({"x": Decimal("0.00")})


def test_decimal_never_serialized_in_exponential_notation():
    payload = canonical_json({"x": Decimal("100")})
    assert b"E" not in payload and b"e" not in payload
    assert b'"100"' in payload


def test_different_values_hash_differently():
    a = canonical_json({"x": Decimal("1.0")})
    b = canonical_json({"x": Decimal("1.01")})
    assert sha256_hex(a) != sha256_hex(b)


def test_aware_datetime_normalized_to_utc_iso():
    a = datetime(2026, 9, 3, 0, 0, tzinfo=timezone.utc)
    payload = canonical_json({"t": a})
    assert b"2026-09-03T00:00:00+00:00" in payload


def test_naive_datetime_rejected():
    with pytest.raises(ValueError):
        canonical_json({"t": datetime(2026, 9, 3, 0, 0)})


def test_uuid_normalized_to_str():
    u = UUID("12345678-1234-5678-1234-567812345678")
    payload = canonical_json({"id": u})
    assert str(u).encode() in payload


def test_nested_list_and_tuple_normalized_identically():
    a = canonical_json({"xs": [Decimal("1.0"), Decimal("2.00")]})
    b = canonical_json({"xs": (Decimal("1.00"), Decimal("2.0"))})
    assert a == b


def test_sha256_hex_is_64_char_hex():
    digest = sha256_hex(canonical_json({"a": 1}))
    assert len(digest) == 64
    int(digest, 16)  # ValueError면 hex가 아님
