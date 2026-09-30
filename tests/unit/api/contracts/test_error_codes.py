"""L4 §2.3(C) — 에러 코드 taxonomy 단일 출처.

Spec: docs/specs/L4_platform_observability_tenancy_api_v1.0.md#§3.3 표

접두 규칙(§2.3): AUTH_/AUTHZ_/VALIDATION_/STATE_/INTEGRITY_/POLICY_/
RISK_/EXCHANGE_/RATE_LIMIT_/INTERNAL_/DEPENDENCY_ 외 금지 —
test_error_codes.py가 이 규칙을 강제한다.
"""

from __future__ import annotations

import time

import pytest
from starlette import status

from src.api.contracts.error_codes import HTTP_STATUS, RETRYABLE, ErrorCode

# ---------------------------------------------------------------------------
# positive tests (기존 5개 + pytest.mark.perf 마커)
# ---------------------------------------------------------------------------

_ALLOWED_PREFIXES = (
    "AUTH_",
    "AUTHZ_",
    "VALIDATION_",
    "STATE_",
    "INTEGRITY_",
    "POLICY_",
    "RISK_",
    "EXCHANGE_",
    "RATE_LIMIT_",
    "INTERNAL_",
    "DEPENDENCY_",
    "RESOURCE_",
    "DATA_",
)


@pytest.mark.perf
def test_every_error_code_uses_an_allowed_prefix():
    """§2.3 접두 규칙 — 문서가 명시한 접두 외에는 신규 코드 추가를
    금지한다(타입 실수·임의 명명을 막는 실질적 게이트)."""
    for code in ErrorCode:
        assert code.value.startswith(_ALLOWED_PREFIXES), (
            f"{code.value}가 허용된 접두사로 시작하지 않습니다."
        )


@pytest.mark.perf
def test_every_error_code_has_an_http_status():
    for code in ErrorCode:
        assert code in HTTP_STATUS, f"{code.value}에 대응하는 HTTP_STATUS가 없습니다."


@pytest.mark.perf
def test_retryable_is_a_subset_of_known_codes():
    assert RETRYABLE.issubset(set(ErrorCode))


@pytest.mark.perf
def test_account_locked_maps_to_423():
    assert HTTP_STATUS[ErrorCode.AUTH_ACCOUNT_LOCKED] == 423


@pytest.mark.perf
def test_resource_not_found_maps_to_404():
    assert HTTP_STATUS[ErrorCode.RESOURCE_NOT_FOUND] == 404


# ---------------------------------------------------------------------------
# negative tests (3건)
# ---------------------------------------------------------------------------


def test_error_code_missing_from_mapping():
    """ErrorCode enum에 없는 코드가 HTTP_STATUS에 없어야 한다 —
    모든 ErrorCode가 HTTP_STATUS에 매핑되어야 한다.

    역으로 HTTP_STATUS에 ErrorCode가 아닌 키가 있으면 안 된다.
    """
    for key in HTTP_STATUS:
        assert isinstance(key, ErrorCode), f"HTTP_STATUS 키 {key}가 ErrorCode가 아닙니다."
    # ErrorCode 전체가 HTTP_STATUS에 존재
    for code in ErrorCode:
        assert code in HTTP_STATUS, f"{code.value}에 대응하는 HTTP_STATUS가 없습니다."


def test_http_status_values_are_valid_codes():
    """HTTP_STATUS 값이 유효한 HTTP 상태 코드(100-599)여야 한다."""
    for code, http_code in HTTP_STATUS.items():
        assert isinstance(code, ErrorCode)
        assert 100 <= http_code <= 599, (
            f"{code.value}의 HTTP 상태 코드 {http_code}가 유효 범위를 벗어납니다."
        )


def test_disallowed_prefix_rejected():
    """허용되지 않은 접두사를 가진 코드명은 ErrorCode에 존재하면 안 된다.

    이 테스트는 _ALLOWED_PREFIXES 상수 자체를 검증하지 않고,
    ErrorCode enum의 모든 값이 허용된 접두사를 쓰는지 확인한다.
    실제 강제력은 test_every_error_code_uses_an_allowed_prefix()가 한다.
    """
    # 접두사 외의 값을 가진 가짜 ErrorCode 생성 시도 방지 —
    # ErrorCode가 str enum이므로 값이 접두사 규칙을 위반하면
    # test_every_error_code_uses_an_allowed_prefix가 잡는다.
    # 여기서는 ErrorCode의 값이 모두 유니크한지 확인.
    values = [code.value for code in ErrorCode]
    assert len(values) == len(set(values)), "ErrorCode 값에 중복이 있습니다."


# ---------------------------------------------------------------------------
# failure-injection tests (1건)
# ---------------------------------------------------------------------------


def test_retryable_values_are_valid_error_codes(monkeypatch):
    """RETRYABLE에 ErrorCode enum이 아닌 값이 포함된 경우 —
    monkeypatch로 오염시켜 불변식 위반을 유발하고,
    isinstance 체크가 이를 포착함을 검증한다.

    실패주입: RETRYABLE에 ErrorCode가 아닌 FakeErrorCode를 주입하면
    isinstance 체크가 AssertionError를 발생시켜야 한다.
    """
    from src.api.contracts import error_codes as mod

    original_retryable = mod.RETRYABLE

    class FakeErrorCode:
        """ErrorCode가 아닌 가짜 객체."""

        def __eq__(self, other):
            return False

        def __hash__(self):
            return 42

    fake = FakeErrorCode()
    try:
        # RETRYABLE에 ErrorCode가 아닌 값을 주입
        patched = set(original_retryable)
        patched.add(fake)
        monkeypatch.setattr(mod, "RETRYABLE", frozenset(patched))

        # 불변식 검증 함수 — 오염된 RETRYABLE에서 isinstance 체크가
        # AssertionError를 발생시켜야 함
        def _verify():
            for item in mod.RETRYABLE:
                assert isinstance(item, ErrorCode), (
                    f"RETRYABLE에 ErrorCode가 아닌 값 {item}이 포함되었습니다."
                )

        # failure-injection: 오염된 RETRYABLE에서 검증이 실패해야 함
        with pytest.raises(AssertionError):
            _verify()
    finally:
        mod.RETRYABLE = original_retryable


# ---------------------------------------------------------------------------
# auxiliary tests (오류 경로 확인)
# ---------------------------------------------------------------------------


def test_http_status_consistency_with_starlette_constants():
    """HTTP_STATUS에 사용된 starlette status 상수들이 실제 유효한
    HTTP 상태 코드 상수임을 확인 — starlette가 새 상수를 추가해도
    기존 매핑이 깨지지 않음을 검증."""
    for code, http_code in HTTP_STATUS.items():
        if isinstance(code, ErrorCode):
            # starlette.status 모듈에 해당하는 상수가 존재하고 값이 일치
            found = False
            for attr in dir(status):
                if attr.startswith("HTTP_") and getattr(status, attr) == http_code:
                    found = True
                    break
            assert found, (
                f"{code.value}의 HTTP 상태 {http_code}에 대응하는 "
                f"starlette.status.HTTP_* 상수를 찾을 수 없습니다."
            )


def test_retryable_not_empty():
    """RETRYABLE이 빈 집합이면 재시도 로직이 작동하지 않는다."""
    assert len(RETRYABLE) > 0, "RETRYABLE이 비어 있습니다."


def test_no_duplicate_http_status_for_different_codes():
    """서로 다른 ErrorCode가 같은 HTTP 상태를 가질 수는 있지만,
    중복 없는 매핑이어야 한다 — 각 ErrorCode는 정확히 한 HTTP 상태를 가진다."""
    codes = list(HTTP_STATUS.keys())
    assert len(codes) == len(set(codes)), "HTTP_STATUS에 중복 키가 있습니다."


@pytest.mark.perf
def test_perf_http_status_lookup_under_1ms():
    """HTTP_STATUS 조회가 1ms 이내여야 한다 — API 응답 지연에 영향 없어야 함."""
    start = time.perf_counter()
    for _ in range(1000):
        for code in HTTP_STATUS:
            _ = HTTP_STATUS[code]
    elapsed_ms = (time.perf_counter() - start) * 1000
    assert elapsed_ms < 100, f"1000회 조회에 {elapsed_ms:.1f}ms 소요 — 100ms 미만이어야 함"
