"""L4-12 — Bitget venue 바디 레벨 오류코드(`code` 필드) → ExchangeErrorKind.

Spec: docs/specs/L4_execution_oms_and_exchange_v1.0.md#L4-12, #L4-31

문서 조사(2026-08-28, bitget/adapter.py 원 주석 근거) 기준으로 확인된
서명/인증 오류코드만 AUTH로 분류한다. 그 외 `"00000"`이 아닌 코드는
기본적으로 SERVER_ERROR(재시도 가능)로 분류한다 — L4-11의 HTTP 상태코드
분류(classify_http)는 미지 상태코드를 UNKNOWN_RESPONSE/retryable=False로
fail-closed 처리하지만, 이 바디 레벨 코드는 그 원칙을 그대로 옮기지
않는다: 과거부터 "00000이 아니면 일단 재시도 가능"으로 운용돼 왔고
(`tests/integration/test_bitget_adapter.py::
test_api_error_response_raises_retryable_by_default`), 이 리프의 DoD는
그 기존 동작을 무수정으로 유지하는 것이다. 실 API 키로 개별 코드를
검증한 뒤에야 이 표를 안전하게 좁힐 수 있다(미검증).

2026-09-09 실키 실측(task-2514) — 40085("Unified Account mode, Classic
Account API not supported")와 40099("exchange environment is incorrect")
는 위 기본값(SERVER_ERROR, 재시도 가능)으로 두면 안 된다: 둘 다 계정
모드/환경 설정이 이 호출과 맞지 않는다는 뜻이라 몇 번을 재시도해도
그대로 실패한다. AUTH로 분류해 `retryable=False`를 명시한다 — "서명이
틀렸다"는 좁은 의미가 아니라 "이 자격증명으로는 이 API 표면에 접근할 수
없다"는 넓은 의미의 인증 경계 실패로 본다. 40085는 추가로
`account_mode.requires_unified_switch()`가 계정 모드 전환 신호로도 쓴다.
"""
from __future__ import annotations

from src.exchanges.common.error_taxonomy import ExchangeErrorKind

SUCCESS_CODE = "00000"

# 서명 오류(40012) / API 키 없음(40037) — 문서 조사 기준, 재시도 무의미.
# 40085(Classic API가 UTA 계정에서 거부됨) / 40099(데모·라이브 환경 불일치)
# — 실측(task-2514), 둘 다 재시도 무의미.
_AUTH_CODES = frozenset({"40012", "40037", "40085", "40099"})


def classify_body_code(code: str | None) -> ExchangeErrorKind:
    """`code`가 `SUCCESS_CODE`가 아닐 때만 호출한다."""
    if code in _AUTH_CODES:
        return ExchangeErrorKind.AUTH
    return ExchangeErrorKind.SERVER_ERROR
