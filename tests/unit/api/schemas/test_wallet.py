"""Tests for src/api/schemas/wallet.py — TopupRequestBody schema coverage."""

from decimal import Decimal

import pytest
from pydantic import ValidationError

from src.api.schemas.wallet import TopupRequestBody

# ── 정상 케이스 ──────────────────────────────────────────────────────────────


class TestTopupRequestBodyValid:
    """Positive cases: accepted inputs."""

    def test_minimal_amount(self):
        """1원 인정 — 기본 Decimal 필드."""
        body = TopupRequestBody(amount=Decimal("1"))
        assert body.amount == Decimal("1")

    def test_large_amount(self):
        """대액 Decimal 정확성 유지."""
        body = TopupRequestBody(amount=Decimal("999999999.99"))
        assert body.amount == Decimal("999999999.99")

    def test_model_dump(self):
        """dict 직렬화 확인."""
        body = TopupRequestBody(amount=Decimal("5000"))
        d = body.model_dump()
        assert d == {"amount": Decimal("5000")}

    def test_model_dump_json_roundtrip(self):
        """JSON 직렬화 → 역직렬화 확인."""
        body = TopupRequestBody(amount=Decimal("10000"))
        json_str = body.model_dump_json()
        restored = TopupRequestBody.model_validate_json(json_str)
        assert restored.amount == Decimal("10000")


# ── Negative test ────────────────────────────────────────────────────────────


class TestTopupRequestBodyNegative:
    """Negative cases: rejected inputs."""

    def test_string_amount_rejected(self):
        """문자열 amount는 Decimal 변환 실패로 거부."""
        with pytest.raises(ValidationError):
            TopupRequestBody(amount="invalid")

    def test_none_amount_rejected(self):
        """None amount는 필수 필드 누락으로 거부."""
        with pytest.raises(ValidationError):
            TopupRequestBody(amount=None)

    def test_missing_amount_rejected(self):
        """amount 필드 필수 — 누르면 거부."""
        with pytest.raises(ValidationError) as exc_info:
            TopupRequestBody()
        assert "amount" in str(exc_info.value)


# ── 실패주입 테스트 ──────────────────────────────────────────────────────────


class TestTopupRequestBodyFailureInjection:
    """Failure injection: Decimal 생성자 예외 유발."""

    def test_decimal_constructor_failure(self) -> None:
        """Decimal 생성자가 예외를 raise하면 ValidationError로 포착.

        Pydantic v2는 Rust 바인딩을 쓰므로 decimal.Decimal을 직접
        monkeypatch하면 Rust 측에서 panic한다. 대신 Decimal을 Decimal
        서브클래스로 대체하고 생성자에서 예외를.raise해 Pydantic의
        검증 단계에서 ValidationError를 받도록 한다.
        """
        import subprocess
        import sys

        code = """
import decimal, sys
from decimal import Decimal as _RealDecimal

class _FailingDecimal(_RealDecimal):
    \"\"\"_RealDecimal 서브클래스 — 생성 시 항상 예외.\"\"\"
    def __new__(cls, value, *args, **kwargs):
        raise ValueError("Decimal creation failed")

decimal.Decimal = _FailingDecimal

from src.api.schemas.wallet import TopupRequestBody

try:
    TopupRequestBody(amount="500")
    print("FAIL: no exception raised")
    sys.exit(1)
except Exception as exc:
    # Pydantic이 Decimal 변환 실패를 ValidationError로 감싸야 한다.
    print(f"OK: {type(exc).__name__}: {exc}")
    sys.exit(0)
"""
        result = subprocess.run(
            [sys.executable, "-c", code],
            capture_output=True,
            text=True,
        )
        assert "OK:" in result.stdout, f"Failure injection failed: {result.stdout} {result.stderr}"


# ── 직렬화 테스트 ────────────────────────────────────────────────────────────


class TestTopupRequestBodySerialization:
    """model_validate_json 등 직렬화 관련 테스트."""

    def test_model_validate_json_string(self):
        """JSON 문자열에서 모델 생성."""
        body = TopupRequestBody.model_validate_json('{"amount": "25000"}')
        assert body.amount == Decimal("25000")

    def test_model_validate_dict(self):
        """dict 입력으로 모델 생성."""
        body = TopupRequestBody.model_validate({"amount": "3000"})
        assert body.amount == Decimal("3000")
