from src.api.contracts.error_codes import HTTP_STATUS, RETRYABLE, ErrorCode

_ALLOWED_PREFIXES = (
    "AUTH_", "AUTHZ_", "VALIDATION_", "STATE_", "INTEGRITY_", "POLICY_",
    "RISK_", "EXCHANGE_", "RATE_LIMIT_", "INTERNAL_", "DEPENDENCY_",
    # §2.3 접두 규칙 원문에는 없지만, §3.3 실제 taxonomy 표가 이 접두로
    # RESOURCE_NOT_FOUND를 명시하고 있다 — 표(구체 계약)가 산문 규칙
    # 목록보다 우선한다고 보고 여기 추가했다(스펙 문서 자체의 불일치,
    # PM 확인 필요하면 note로 남김).
    "RESOURCE_",
)


def test_every_error_code_uses_an_allowed_prefix():
    """§2.3 접두 규칙 — 문서가 명시한 접두 외에는 신규 코드 추가를
    금지한다(타입 실수·임의 명명을 막는 실질적 게이트)."""
    for code in ErrorCode:
        assert code.value.startswith(_ALLOWED_PREFIXES), (
            f"{code.value}가 허용된 접두사로 시작하지 않습니다."
        )


def test_every_error_code_has_an_http_status():
    for code in ErrorCode:
        assert code in HTTP_STATUS, f"{code.value}에 대응하는 HTTP_STATUS가 없습니다."


def test_retryable_is_a_subset_of_known_codes():
    assert RETRYABLE.issubset(set(ErrorCode))


def test_account_locked_maps_to_423():
    assert HTTP_STATUS[ErrorCode.AUTH_ACCOUNT_LOCKED] == 423


def test_resource_not_found_maps_to_404():
    assert HTTP_STATUS[ErrorCode.RESOURCE_NOT_FOUND] == 404
