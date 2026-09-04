"""L4 §2.3(C) — 도메인 예외 → ErrorCode 매핑.

Spec: docs/specs/L4_platform_observability_tenancy_api_v1.0.md#§2.3, §3.3

이관 순서: PLT-108→17→18→20→19→29→task-1108(foundation/connections·
mandates·evidence)→task-1217(foundation/paper_control·performance·
reconciliation)→task-1218(foundation/risk_gate·trust·validation).

PLT-20 decision(task-1017)의 "공용 파일 수정 금지"보다 "기존 테스트 무수정
통과"를 우선해(신규 ErrorCode 없이) alert/device_token 예외를 등록했다
(상세는 task-1017 note 참조).

한계(정직하게 명시): 서비스당 예외 클래스 하나로 묶인 타입(UserAdminError
등)은 대표 사유 하나로만 매핑된다 — 라우터가 실제로 상태코드를 구분해야
했던 경우만(StrategyNotFoundError 등) 서브클래스로 쪼갰다.

AuthError는 AUTH_INVALID_CREDENTIALS 하나로만 매핑한다 — 계정 미존재/잠금/
정지/틀린 비밀번호를 상태코드로 구분하면 계정열거 사이드채널이 된다
(레드팀 감사 #12).

`STATUS_OVERRIDE`: 기존 라우터가 쓰던 개별 상태코드(402/501/422 등)를 새
ErrorCode 없이 유지한다(`map_exception()` 3-tuple 시그니처 불변).

EXCEPTION_MAP/STATUS_OVERRIDE 데이터와 그 재료가 되는 도메인 예외 import는
`exception_registry.py`(services/auth)·`exception_registry_foundation.py`
(foundation/*) 두 자매 모듈에 있다 — 이 파일 하나가 아키텍처 가드
P6.line_cap(300줄) 상한에 닿아 task-1218에서 분리했다(공개 API는 그대로 이
모듈 경로에 남긴다 — 다른 모듈이 `from src.api.contracts.exception_mapping
import X`로 쓰던 클래스/함수 이름이 전부 그대로다)."""
from __future__ import annotations

from src.api.contracts.error_codes import ErrorCode
from src.api.contracts.exception_registry import (
    EXCEPTION_MAP_SERVICES,
    STATUS_OVERRIDE_SERVICES,
    ApprovalOwnershipError,
    SessionRevokedError,
)
from src.api.contracts.exception_registry_foundation import (
    EXCEPTION_MAP_FOUNDATION,
    STATUS_OVERRIDE_FOUNDATION,
    ConsentNotFoundError,
    UnsupportedStatementScopeError,
)

__all__ = [
    "ApprovalOwnershipError",
    "ConsentNotFoundError",
    "SessionRevokedError",
    "UnsupportedStatementScopeError",
    "map_exception",
    "override_status",
]

EXCEPTION_MAP: list[tuple[type[Exception], ErrorCode]] = (
    EXCEPTION_MAP_SERVICES + EXCEPTION_MAP_FOUNDATION
)
STATUS_OVERRIDE: list[tuple[type[Exception], int]] = (
    STATUS_OVERRIDE_SERVICES + STATUS_OVERRIDE_FOUNDATION
)


def map_exception(exc: Exception) -> tuple[ErrorCode, str, dict[str, object]]:
    """(ErrorCode, message, details) — details는 지금은 항상 빈 dict다
    (§3.3 details.fields/reason_codes 채우기는 검증기·정책엔진 쪽에
    구조화된 사유가 생기는 후속 리프에서)."""
    for exc_type, code in EXCEPTION_MAP:
        if isinstance(exc, exc_type):
            return code, str(exc), {}
    return ErrorCode.INTERNAL_ERROR, str(exc), {}


def override_status(exc: Exception) -> int | None:
    """`STATUS_OVERRIDE`에 등록된 예외면 그 상태코드를, 아니면 None을
    반환한다 — None이면 호출자가 `HTTP_STATUS[code]` 기본값을 쓴다."""
    for exc_type, status_code in STATUS_OVERRIDE:
        if isinstance(exc, exc_type):
            return status_code
    return None
