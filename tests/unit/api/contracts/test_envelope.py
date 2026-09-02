from uuid import UUID

from pydantic import BaseModel

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
