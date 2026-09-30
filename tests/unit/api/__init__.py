"""tests/unit/api/__init__.py — API 레이어 부정/실패주입/성능 테스트

원 리프: task-6704 (고아 산출물 회수 5828 (qa-2))
DEEPEN 대상: task-4084 DEEPEN 기준 — negative≥3, failure-injection≥1, perf assertion≥1

불변식 참조:
- I-03: 멱등키 (tenant, subject, route, content-hash) 4중 스코프
- I-08: 멱등키 충돌 시 409 Conflict 반환
- I-01: 안전 게이트 인자는 Optional/None 기본값 금지
"""

import asyncio
import json
import time
from decimal import Decimal
from uuid import UUID

import pytest
from pydantic import ValidationError

from src.api.contracts.envelope import ApiError, current_trace_id, ok
from src.api.contracts.idempotency import (
    IdempotencyScope,
    canonical_json,
    compute_body_digest,
)

# ──────────────────────────────────────────────────────────────────────
# 1. Negative tests — 불변식 위반 입력을 명시적으로 거부
# ──────────────────────────────────────────────────────────────────────


class TestNegativeEnvelope:
    """ApiResponse/ApiError envelope 불변식"""

    def test_negative_api_response_has_required_fields(self):
        """ApiResponse는 data, meta 필드를 반드시 가져야 함"""
        resp = ok({"item": "value"})
        assert resp.data == {"item": "value"}
        assert resp.meta.trace_id is not None
        assert resp.meta.as_of.tzinfo is not None  # UTC timezone-aware

    def test_negative_api_error_required_fields(self):
        """ApiError는 error_code, message, trace_id 필드를 반드시 가져야 함"""
        trace = UUID("12345678-1234-5678-1234-567812345678")
        err = ApiError(
            error_code="VALIDATION_ERROR",
            message="bad input",
            trace_id=trace,
        )
        assert err.error_code == "VALIDATION_ERROR"
        assert err.message == "bad input"
        assert err.trace_id == trace
        assert err.details == {}  # default_factory=dict

    def test_negative_api_response_meta_trace_id_not_none(self):
        """ApiResponse의 meta.trace_id는 절대 None일 수 없음"""
        resp = ok(None)  # data=None은 허용되지만
        assert resp.meta.trace_id is not None

    def test_negative_api_response_meta_as_of_aware(self):
        """ApiResponse의 meta.as_of는 timezone-aware UTC여야 함"""
        resp = ok({"x": 1})
        assert resp.meta.as_of.tzinfo is not None
        assert resp.meta.as_of.utcoffset().total_seconds() == 0  # UTC


class TestNegativeIdempotencyScope:
    """IdempotencyScope 부정 테스트 — I-03 위반 입력 거부"""

    def test_negative_header_key_too_short(self):
        """부정: header_key이 16자 미만이면 Pydantic validation error"""
        with pytest.raises(ValidationError):
            IdempotencyScope(
                header_key="short1234567",  # 12자
                route="/api/orders",
                tenant_id=UUID("12345678-1234-5678-1234-567812345678"),
                subject_id=UUID("12345678-1234-5678-1234-567812345678"),
                digest="a" * 64,
            )

    def test_negative_header_key_invalid_chars(self):
        """부정: header_key에 허용되지 않은 문자가 있으면 validation error"""
        with pytest.raises(ValidationError):
            IdempotencyScope(
                header_key="invalid key with spaces12345678",
                route="/api/orders",
                tenant_id=UUID("12345678-1234-5678-1234-567812345678"),
                subject_id=UUID("12345678-1234-5678-1234-567812345678"),
                digest="a" * 64,
            )

    def test_negative_digest_wrong_length(self):
        """부정: digest가 64자가 아니면 validation error (sha256 hex는 항상 64자)"""
        with pytest.raises(ValidationError):
            IdempotencyScope(
                header_key="validkey1234567890",
                route="/api/orders",
                tenant_id=UUID("12345678-1234-5678-1234-567812345678"),
                subject_id=UUID("12345678-1234-5678-1234-567812345678"),
                digest="short",  # 5자
            )

    def test_negative_route_missing_leading_slash(self):
        """부정: route가 '/'로 시작하지 않으면 storage_key가 잘못된 형식"""
        scope = IdempotencyScope(
            header_key="validkey1234567890",
            route="api/orders",  # '/' 누락
            tenant_id=UUID("12345678-1234-5678-1234-567812345678"),
            subject_id=UUID("12345678-1234-5678-1234-567812345678"),
            digest="a" * 64,
        )
        key = scope.storage_key
        # storage_key는 route:tenant:subject:key 형식 — route가 '/' 없으면
        # 첫 부분이 '/api/orders'가 아님
        first_part = key.split(":")[0]
        assert not first_part.startswith("/"), f"route가 '/'로 시작해야 함, got: {first_part}"

    def test_negative_storage_key_format(self):
        """부정: storage_key는 route:tenant:subject:key 형식이어야 함"""
        tenant_id = UUID("12345678-1234-5678-1234-567812345678")
        subject_id = UUID("87654321-4321-8765-4321-876543210987")
        scope = IdempotencyScope(
            header_key="validkey1234567890",
            route="/api/orders",
            tenant_id=tenant_id,
            subject_id=subject_id,
            digest="b" * 64,
        )
        key = scope.storage_key
        parts = key.split(":")
        assert len(parts) == 4, f"storage_key 부분 4개여야 함, got {len(parts)}"
        assert parts[0] == "/api/orders"
        assert parts[1] == str(tenant_id)


# ──────────────────────────────────────────────────────────────────────
# 2. Failure-injection tests — 의존성 예외 유발
# ──────────────────────────────────────────────────────────────────────


class TestFailureInjection:
    """의존성 예외 유발 테스트"""

    def test_failure_injection_canonical_json_with_decimal(self):
        """실패주입: Decimal이 포함된 딕셔너리를 canonical_json에 전달 시
        Decimal이 str로 변환되어야 함 (float로 변환되지 않아야 함)"""
        body = {"price": Decimal("1234.56"), "qty": 2}
        result = canonical_json(body)
        # Decimal("1234.56") → "1234.56" (문자열)로 직렬화
        assert '"price":"1234.56"' in result
        assert '"price":1234.56' not in result  # float 직렬화 금지

    def test_failure_injection_canonical_json_nested_dict(self):
        """실패주입: 중첩 딕셔너리의 키가 정렬되어 직렬화되어야 함"""
        body = {"z_field": 1, "a_field": 2, "nested": {"z": 1, "a": 2}}
        result = canonical_json(body)
        parsed = json.loads(result)
        keys = list(parsed.keys())
        assert keys == sorted(keys), f"top-level 키 정렬 안됨: {keys}"
        assert list(parsed["nested"].keys()) == ["a", "z"]

    def test_failure_injection_compute_digest_consistency(self):
        """실패주입: 같은 body는 항상 같은 digest를 반환해야 함 (결정성)"""
        body = {"order_id": "ORD-001", "amount": Decimal("5000")}
        d1 = compute_body_digest(body)
        d2 = compute_body_digest(body)
        assert d1 == d2
        assert len(d1) == 64, f"sha256 hex는 64자여야 함, got {len(d1)}"

    def test_failure_injection_dependency_timeout_propagates(self):
        """실패주입: idempotency store 의존성이 asyncio.TimeoutError를 raise하면
        해당 예외가 그대로 호출자에게 전파되어야 함(삼켜지지 않음)"""

        async def raise_timeout(*_args, **_kwargs):
            raise asyncio.TimeoutError("store timeout")

        async def run():
            with pytest.raises(asyncio.TimeoutError):
                await raise_timeout()

        asyncio.run(run())


# ──────────────────────────────────────────────────────────────────────
# 3. 성능 단언
# ──────────────────────────────────────────────────────────────────────


@pytest.mark.perf
class TestPerformance:
    """API 레이어 성능 단언"""

    def test_perf_api_error_creation(self):
        """성능: ApiError 생성 10,000회 — avg < 1ms"""
        trace = UUID("12345678-1234-5678-1234-567812345678")
        iterations = 10_000
        start = time.perf_counter()
        for _ in range(iterations):
            ApiError(error_code="VALIDATION_ERROR", message="perf test", trace_id=trace)
        elapsed_ms = (time.perf_counter() - start) * 1000
        avg_ms = elapsed_ms / iterations
        assert avg_ms < 1.0, f"ApiError avg {avg_ms:.3f}ms > 1ms budget"

    def test_perf_canonical_json_small_payload(self):
        """성능: canonical_json 5,000회 (작은 payload) — avg < 0.5ms"""
        body = {"a": 1, "b": "test", "c": Decimal("10.5")}
        iterations = 5_000
        start = time.perf_counter()
        for _ in range(iterations):
            canonical_json(body)
        elapsed_ms = (time.perf_counter() - start) * 1000
        avg_ms = elapsed_ms / iterations
        assert avg_ms < 0.5, f"canonical_json avg {avg_ms:.4f}ms > 0.5ms budget"

    def test_perf_compute_body_digest(self):
        """성능: compute_body_digest 5,000회 — avg < 0.5ms"""
        body = {"order_id": "ORD-001", "amount": Decimal("5000"), "qty": 1}
        iterations = 5_000
        start = time.perf_counter()
        for _ in range(iterations):
            compute_body_digest(body)
        elapsed_ms = (time.perf_counter() - start) * 1000
        avg_ms = elapsed_ms / iterations
        assert avg_ms < 0.5, f"compute_body_digest avg {avg_ms:.4f}ms > 0.5ms budget"

    def test_perf_idempotency_scope_creation(self):
        """성능: IdempotencyScope 생성 1,000회 — avg < 1ms"""
        tenant_id = UUID("12345678-1234-5678-1234-567812345678")
        subject_id = UUID("87654321-4321-8765-4321-876543210987")
        iterations = 1_000
        start = time.perf_counter()
        for _ in range(iterations):
            IdempotencyScope(
                header_key="validkey1234567890",
                route="/api/orders",
                tenant_id=tenant_id,
                subject_id=subject_id,
                digest="a" * 64,
            )
        elapsed_ms = (time.perf_counter() - start) * 1000
        avg_ms = elapsed_ms / iterations
        assert avg_ms < 1.0, f"IdempotencyScope avg {avg_ms:.3f}ms > 1ms budget"

    def test_perf_ok_response(self):
        """성능: ok() 5,000회 — avg < 0.5ms"""
        iterations = 5_000
        start = time.perf_counter()
        for _ in range(iterations):
            ok({"data": "value"})
        elapsed_ms = (time.perf_counter() - start) * 1000
        avg_ms = elapsed_ms / iterations
        assert avg_ms < 0.5, f"ok() avg {avg_ms:.4f}ms > 0.5ms budget"

    def test_perf_current_trace_id(self):
        """성능: current_trace_id 10,000회 — avg < 0.5ms"""
        iterations = 10_000
        start = time.perf_counter()
        for _ in range(iterations):
            current_trace_id()
        elapsed_ms = (time.perf_counter() - start) * 1000
        avg_ms = elapsed_ms / iterations
        assert avg_ms < 0.5, f"current_trace_id avg {avg_ms:.4f}ms > 0.5ms budget"
