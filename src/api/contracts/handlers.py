"""L4 §2.3(C) — 전역 예외 핸들러.

Spec: docs/specs/L4_platform_observability_tenancy_api_v1.0.md#§2.3

`MihwaError`(src/core/exceptions.py)를 별도로 안 잡는다 — 지금 이
코드베이스의 실제 도메인 예외(AuthError/UserAdminError 등)는 전부
`MihwaError`를 상속하지 않고 각자 독립적으로 `Exception`을 상속한다.
그래서 마지막 `Exception` 핸들러 하나가 `map_exception()`으로 도메인
예외까지 전부 처리한다 — MihwaError를 상속하는 예외가 생기면 그것도
그대로 이 경로를 탄다(타입 매칭이라 상속 관계와 무관하게 동작).
"""
from __future__ import annotations

import logging
from typing import Any

from fastapi import FastAPI, HTTPException, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from src.api.contracts.envelope import ApiError, current_trace_id
from src.api.contracts.error_codes import HTTP_STATUS, ErrorCode
from src.api.contracts.exception_mapping import map_exception
from src.core.logging.request_context import get_current_request_id

logger = logging.getLogger(__name__)

# 레거시 라우터가 raw HTTPException(status, str)을 계속 쓰는 동안(§9
# PLT-2x에서 순차 이관 예정), 상태코드만으로 그럴듯한 기본 코드를
# 골라준다 — 완벽한 매핑이 아니라 "적어도 봉투 모양은 맞춘다"는 목적.
_STATUS_DEFAULT_CODE: dict[int, ErrorCode] = {
    status.HTTP_400_BAD_REQUEST: ErrorCode.VALIDATION_INVALID_FIELD,
    status.HTTP_401_UNAUTHORIZED: ErrorCode.AUTH_REQUIRED,
    status.HTTP_403_FORBIDDEN: ErrorCode.AUTHZ_FORBIDDEN,
    status.HTTP_404_NOT_FOUND: ErrorCode.RESOURCE_NOT_FOUND,
    status.HTTP_409_CONFLICT: ErrorCode.STATE_INVALID_TRANSITION,
    status.HTTP_423_LOCKED: ErrorCode.AUTH_ACCOUNT_LOCKED,
    status.HTTP_429_TOO_MANY_REQUESTS: ErrorCode.RATE_LIMIT_EXCEEDED,
    status.HTTP_502_BAD_GATEWAY: ErrorCode.EXCHANGE_FATAL,
    status.HTTP_503_SERVICE_UNAVAILABLE: ErrorCode.EXCHANGE_UNAVAILABLE,
}


def _error_response(
    code: ErrorCode, message: str, details: dict[str, Any] | None = None
) -> JSONResponse:
    trace_id = current_trace_id()
    error = ApiError(
        error_code=code.value,
        message=message,
        details=details or {},
        trace_id=trace_id,
        retry_after_seconds=None,
    )
    body = error.model_dump(mode="json")
    return JSONResponse(status_code=HTTP_STATUS[code], content=body)


def install_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(HTTPException)
    async def _handle_http_exception(request: Request, exc: HTTPException) -> JSONResponse:
        if isinstance(exc.detail, dict) and "error_code" in exc.detail:
            code = ErrorCode(exc.detail["error_code"])
            message = exc.detail.get("message", "")
        else:
            code = _STATUS_DEFAULT_CODE.get(exc.status_code, ErrorCode.INTERNAL_ERROR)
            message = str(exc.detail)
        return _error_response(code, message)

    @app.exception_handler(RequestValidationError)
    async def _handle_validation_error(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        fields = [".".join(str(part) for part in err["loc"]) for err in exc.errors()]
        return _error_response(
            ErrorCode.VALIDATION_INVALID_FIELD,
            "요청 값이 올바르지 않습니다.",
            details={"fields": fields},
        )

    @app.exception_handler(Exception)
    async def _handle_domain_or_unknown_exception(request: Request, exc: Exception) -> JSONResponse:
        code, message, details = map_exception(exc)
        if code == ErrorCode.INTERNAL_ERROR:
            # 원인은 error 로그에만 — 클라이언트에게는 고정 메시지 + trace_id.
            logger.error(
                "미분류 예외",
                exc_info=exc,
                extra={
                    "event_type": "api.unhandled_exception",
                    "correlation_id": get_current_request_id(),
                },
            )
            message = "일시적인 오류가 발생했습니다. 계속되면 trace_id와 함께 문의해주세요."
            details = {}
        return _error_response(code, message, details)


__all__ = ["install_exception_handlers"]
