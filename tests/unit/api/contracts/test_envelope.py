import json
from datetime import datetime, timezone
from uuid import UUID

import pytest
from pydantic import BaseModel, ValidationError

from scripts.check_openapi_compat import find_violations
from src.api.contracts.envelope import ApiError, ApiResponse, Meta, ok
from src.api.contracts.pagination import PageMeta
from src.core.logging.request_context import request_id_var


class _Payload(BaseModel):
    value: int


def test_ok_wraps_data_and_generates_a_trace_id_outside_request_context():
    response = ok(_Payload(value=1))

    assert response.data.value == 1
    assert isinstance(response.meta.trace_id, UUID)
    assert response.meta.page is None


def test_ok_reuses_request_id_contextvar_as_trace_id_when_present():
    """request_id 미들웨어가 설정한 값과 봉투의 trace_id가 같은 값이어야
    클라이언트가 응답 헤더든 본문이든 같은 요청을 가리킨다."""
    token = request_id_var.set("abcdef1234567890abcdef1234567890")
    try:
        response = ok(_Payload(value=1))
    finally:
        request_id_var.reset(token)

    assert str(response.meta.trace_id) == "abcdef12-3456-7890-abcd-ef1234567890"


def test_ok_attaches_page_meta_when_given():
    page = PageMeta(total=42, page=1, size=20, next_cursor=None)

    response = ok([_Payload(value=1)], page=page)

    assert response.meta.page == page


def test_api_response_and_api_error_are_json_serializable():
    response = ApiResponse[_Payload](
        data=_Payload(value=1),
        meta=Meta(trace_id=UUID(int=0), as_of="2026-01-01T00:00:00+00:00"),
    )
    error = ApiError(error_code="INTERNAL_ERROR", message="문제 발생", trace_id=UUID(int=0))

    assert response.model_dump(mode="json")["data"]["value"] == 1
    assert error.model_dump(mode="json")["error_code"] == "INTERNAL_ERROR"


# ── negative tests (invariant violation → explicit rejection) ──


class TestNegativeTraceIdNone:
    """I-01 (trace_id 불변): Meta/ApiResponse 는 항상 유효한 UUID trace_id 를 가진다.
    명시적 None 은 Pydantic 검증에서 거부해야 한다."""

    @pytest.mark.perf
    def test_meta_rejects_explicit_none_trace_id(self):
        """Meta(trace_id=None) → ValidationError 로 거부."""
        with pytest.raises(ValidationError):
            Meta(trace_id=None, as_of=datetime.now(timezone.utc))

    @pytest.mark.perf
    def test_api_error_rejects_explicit_none_trace_id(self):
        """ApiError(trace_id=None) → ValidationError 로 거부."""
        with pytest.raises(ValidationError):
            ApiError(error_code="TEST", message="msg", trace_id=None)


class TestNegativeAsOfType:
    """I-01 (as_of 시점): Meta.as_of 는 datetime 이어야 한다.
    문자열/숫자 등 타입 오류는 Pydantic 검증에서 거부해야 한다."""

    @pytest.mark.perf
    def test_meta_rejects_string_as_as_of(self):
        """Meta(as_of='not-a-datetime') → ValidationError."""
        with pytest.raises(ValidationError):
            Meta(trace_id=UUID(int=0), as_of="not-a-datetime")

    @pytest.mark.perf
    def test_meta_rejects_none_as_as_of(self):
        """Meta(as_of=None) → ValidationError."""
        with pytest.raises(ValidationError):
            Meta(trace_id=UUID(int=0), as_of=None)

    @pytest.mark.perf
    def test_meta_accepts_tz_aware_utc_as_of(self):
        """Meta(as_of=...+00:00) → 정상 통과 (양성 확인)."""
        meta = Meta(trace_id=UUID(int=0), as_of=datetime(2026, 1, 1, tzinfo=timezone.utc))
        assert meta.as_of.tzinfo is not None


class TestNegativeApiErrorMissingFields:
    """I-01 (ApiError 필수 필드): error_code, message, trace_id 는 모두 필수다."""

    @pytest.mark.perf
    def test_api_error_rejects_missing_error_code(self):
        with pytest.raises(ValidationError):
            ApiError(message="msg", trace_id=UUID(int=0))

    @pytest.mark.perf
    def test_api_error_rejects_missing_message(self):
        with pytest.raises(ValidationError):
            ApiError(error_code="TEST", trace_id=UUID(int=0))

    @pytest.mark.perf
    def test_api_error_rejects_missing_trace_id(self):
        with pytest.raises(ValidationError):
            ApiError(error_code="TEST", message="msg")


class TestFailureInjectionGetRequestIdException:
    """실패주입: get_current_request_id() 가 예외를 raise 할 때 ok() 는
    예외를 전파하지 않고 fallback uuid4() 를 생성해야 한다."""

    @pytest.mark.perf
    def test_ok_fallback_to_uuid4_when_get_current_request_id_raises(self, monkeypatch):
        """monkeypatch get_current_request_id → RuntimeError.
        ok() 가 fallback uuid4() 를 사용해 예외 전파 없이 반환하는지 확인."""
        from src.core.logging import request_context

        monkeypatch.setattr(
            request_context,
            "get_current_request_id",
            lambda: (_ for _ in ()).raise_exception(RuntimeError("request_id_service_down")),
        )

        # ok() 가 예외 없이 반환해야 함
        response = ok(_Payload(value=42))
        assert response.data.value == 42
        # trace_id 는 uuid4() fallback — 유효한 UUID 여야 함
        assert isinstance(response.meta.trace_id, UUID)


# ── 게이트 적색 재현 ──


def test_gate_red_reproduction_meta_trace_id_removed_from_openapi_schema() -> None:
    """게이트적색재현: envelope.Meta 가 실제로 생성하는 OpenAPI 스키마에서
    trace_id 프로퍼티가 삭제되면 check_openapi_compat.find_violations(PLT-16)가
    MAJOR 위반으로 잡아야 한다 — I-01(trace_id 불변)이 openapi 계약 레벨에서도
    기계적으로 지켜지는지 확인한다(단순 pydantic ValidationError 재현이 아니라
    실제 게이트 스크립트를 호출한다)."""
    meta_properties = Meta.model_json_schema()["properties"]
    meta_schema = {
        "type": "object",
        "properties": {k: v for k, v in meta_properties.items() if k != "page"},
    }
    payload_schema = {"type": "object", "properties": {"value": {"type": "integer"}}}
    envelope_schema = {
        "type": "object",
        "properties": {
            "data": {"$ref": "#/components/schemas/_Payload"},
            "meta": {"$ref": "#/components/schemas/Meta"},
        },
    }
    paths = {
        "/foo": {
            "get": {
                "responses": {
                    "200": {
                        "content": {
                            "application/json": {
                                "schema": {"$ref": "#/components/schemas/ApiResponse__Payload_"}
                            }
                        }
                    }
                }
            }
        }
    }
    schemas = {
        "ApiResponse__Payload_": envelope_schema,
        "_Payload": payload_schema,
        "Meta": meta_schema,
    }
    baseline = {"paths": paths, "components": {"schemas": schemas}}
    current = json.loads(json.dumps(baseline))
    current["components"]["schemas"]["Meta"]["properties"].pop("trace_id")

    violations = find_violations(baseline, current)

    assert violations != [], "Meta.trace_id 삭제는 게이트가 MAJOR 위반으로 잡아야 한다"
