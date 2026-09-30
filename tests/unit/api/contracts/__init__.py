"""tests/unit/api/contracts/__init__.py — contracts 패키지 통합/부정/실패주입 테스트

원 리프: task-6704 (고아 산출물 회수 5828 (qa-2))
DEEPEN 대상: task-4084 DEEPEN 기준 — negative>=3, failure-injection>=1, perf assertion>=1

불변식 참조:
- I-03: 멱등키 (tenant, actor, route, content-hash) 4중 스코프
- I-08: 멱등키 충돌 시 409 Conflict 반환
"""

import json
import time
from decimal import Decimal
from uuid import uuid4

import pytest
from pydantic import ValidationError

from src.api.contracts.envelope import ApiError, ok
from src.api.contracts.error_codes import HTTP_STATUS, RETRYABLE, ErrorCode
from src.api.contracts.idempotency import (
    IdempotencyScope,
    canonical_json,
    compute_body_digest,
)
from src.api.contracts.pagination import PageMeta, PageParams

# ──────────────────────────────────────────────────────────────────────
# 1. Negative tests — 불변식 위반 입력을 명시적으로 거부
# ──────────────────────────────────────────────────────────────────────


class TestNegativeCanonicalJson:
    """canonical_json — 키 정렬 및 Decimal 직렬화 부정 테스트"""

    def test_negative_canonical_json_keys_sorted(self):
        """부정: 키가 알파벳 순으로 정렬되어 반환되어야 함"""
        body = {"z": 1, "a": 2, "m": 3}
        result = canonical_json(body)
        parsed = json.loads(result)
        keys = list(parsed.keys())
        assert keys == sorted(keys), "키가 정렬되지 않음"

    def test_negative_canonical_json_decimal_stringified(self):
        """부정: Decimal 값은 문자열로 직렬화되어야 함 (float 금지)"""
        body = {"price": Decimal("1234.56")}
        result = canonical_json(body)
        # Decimal은 str로 직렬화되므로 "1234.56"이 나와야 함
        assert '"price":"1234.56"' in result
        assert '"price":1234.56' not in result  # float 형태가 없어야 함

    def test_negative_canonical_json_nested_lists_preserved(self):
        """부정: 리스트 요소 순서는 유지되어야 함 (키 정렬이 리스트를 뒤섞지 않음)"""
        body = {"items": [3, 1, 2]}
        result = canonical_json(body)
        parsed = json.loads(result)
        assert parsed["items"] == [3, 1, 2]


class TestNegativeIdempotencyScope:
    """IdempotencyScope — 필드 제약 부정 테스트"""

    def test_negative_header_key_below_min_length(self):
        """부정: header_key가 16자 미만이면 Pydantic ValidationError"""
        with pytest.raises(ValidationError):
            IdempotencyScope(
                header_key="short123456789",  # 14자
                route="/api/orders",
                tenant_id=uuid4(),
                subject_id=uuid4(),
                digest="a" * 64,
            )

    def test_negative_header_key_invalid_chars(self):
        """부정: header_key에 특수문자(/, space)가 포함되면 ValidationError"""
        with pytest.raises(ValidationError):
            IdempotencyScope(
                header_key="invalid-key/with/slash",
                route="/api/orders",
                tenant_id=uuid4(),
                subject_id=uuid4(),
                digest="a" * 64,
            )

    def test_negative_digest_wrong_length(self):
        """부정: digest가 64자가 아니면 Pydantic ValidationError"""
        with pytest.raises(ValidationError):
            IdempotencyScope(
                header_key="validkey1234567890",
                route="/api/orders",
                tenant_id=uuid4(),
                subject_id=uuid4(),
                digest="short",  # 5자
            )


class TestNegativePageParams:
    """PageParams — 페이지 파라미터 제약 부정 테스트"""

    def test_negative_page_too_small(self):
        """부정: page가 1 미만이면 ValidationError"""
        with pytest.raises(ValidationError):
            PageParams(page=0, size=10)

    def test_negative_size_too_large(self):
        """부정: size가 100을 초과하면 ValidationError"""
        with pytest.raises(ValidationError):
            PageParams(page=1, size=101)


class TestNegativeApiResponseInvariant:
    """ApiResponse — envelope 불변식 부정 테스트"""

    def test_negative_ok_returns_data_field(self):
        """부정: ok()는 data 필드를 반드시 반환해야 함"""
        resp = ok({"key": "value"})
        assert hasattr(resp, "data")
        assert resp.data == {"key": "value"}

    def test_negative_ok_returns_meta_with_trace_id(self):
        """부정: ok()의 meta.trace_id는 None이 될 수 없음"""
        resp = ok(None)
        assert resp.meta.trace_id is not None


# ──────────────────────────────────────────────────────────────────────
# 2. Failure-injection tests — 의존성 예외 유발
# ──────────────────────────────────────────────────────────────────────


class TestFailureInjectionDigest:
    """compute_body_digest — 비정상 입력에 대한 실패주입"""

    def test_failure_injection_set_body(self):
        """실패주입: set은 JSON 직렬화 불가능이라 TypeError 발생"""
        with pytest.raises(TypeError):
            compute_body_digest({"tags": {1, 2, 3}})

    def test_failure_injection_non_serializable_body(self):
        """실패주입: JSON 직렬화 불가능 객체를 전달하면 TypeError 발생"""

        class Unserializable:
            pass

        with pytest.raises(TypeError):
            compute_body_digest(Unserializable())


# ──────────────────────────────────────────────────────────────────────
# 3. Performance assertion
# ──────────────────────────────────────────────────────────────────────


class TestPerformanceDigest:
    """compute_body_digest — 성능 단언"""

    @pytest.mark.perf
    def test_perf_digest_throughput(self):
        """성단언: 1000회 digest 계산이 0.5초 미만이어야 함 (DEEPEN 성능 예산)"""
        body = {"item": "test", "price": Decimal("999.99"), "qty": 10}
        iterations = 1000
        start = time.perf_counter()
        for _ in range(iterations):
            compute_body_digest(body)
        elapsed = time.perf_counter() - start
        assert elapsed < 0.5, f"digest {iterations}회 계산에 {elapsed:.3f}s -- 예산 0.5s 초과"


# ──────────────────────────────────────────────────────────────────────
# 4. Cross-module integration tests
# ──────────────────────────────────────────────────────────────────────


class TestIntegrationErrorCodeEnvelope:
    """ErrorCode + ApiError — 에러 코드와 envelope의 통합 테스트"""

    def test_integration_error_code_to_api_error(self):
        """통합: ErrorCode 값을 ApiError에 매핑하여 trace_id를 붙일 수 있어야 함"""
        trace = uuid4()
        err = ApiError(
            error_code=ErrorCode.VALIDATION_INVALID_FIELD.value,
            message="필드 'price'가 필요합니다.",
            trace_id=trace,
        )
        assert err.error_code == "VALIDATION_INVALID_FIELD"
        assert err.trace_id == trace

    def test_integration_http_status_matches_error_code(self):
        """통합: HTTP_STATUS[ErrorCode]가 4xx/5xx 범위에 있어야 함"""
        for code in ErrorCode:
            status_code = HTTP_STATUS.get(code, 500)
            assert 400 <= status_code <= 599, f"{code}의 HTTP_STATUS {status_code}가 4xx/5xx 아님"


class TestIntegrationRetryable:
    """ErrorCode + RETRYABLE — 재시도 여부 통합 테스트"""

    def test_integration_retryable_contains_server_errors(self):
        """통합: 서버 오류(503, 429)는 RETRYABLE에 포함되어야 함"""
        assert ErrorCode.EXCHANGE_UNAVAILABLE in RETRYABLE
        assert ErrorCode.RATE_LIMIT_EXCEEDED in RETRYABLE

    def test_integration_retryable_excludes_client_errors(self):
        """통합: 클라이언트 오류(400, 401, 403 등)는 RETRYABLE에 포함되지 않아야 함"""
        assert ErrorCode.VALIDATION_INVALID_FIELD not in RETRYABLE
        assert ErrorCode.AUTH_REQUIRED not in RETRYABLE
        assert ErrorCode.AUTHZ_FORBIDDEN not in RETRYABLE


class TestIntegrationPagination:
    """PageParams + PageMeta — 페이지네이션 메타데이터 통합 테스트"""

    def test_integration_page_meta_from_params(self):
        """통합: PageMeta가 PageParams 기반으로 올바르게 계산되어야 함"""
        params = PageParams(page=2, size=10)
        total = 45
        meta = PageMeta(total=total, page=params.page, size=params.size)
        assert meta.total == total
        assert meta.page == 2
        assert meta.size == 10

    def test_integration_page_meta_fields(self):
        """통합: PageMeta가 total, page, size, next_cursor 필드를 가져야 함"""
        meta = PageMeta(total=42, page=5, size=10)
        assert meta.total == 42
        assert meta.page == 5
        assert meta.size == 10
        assert meta.next_cursor is None

    def test_integration_page_params_default_values(self):
        """통합: PageParams 기본값이 안전해야 함"""
        params = PageParams()
        assert params.page == 1
        assert params.size == 20
