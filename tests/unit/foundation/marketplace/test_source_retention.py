"""SRC-1 (ADR-2026-09-26-B Decision 2) -- marketplace/domain/visibility
`validate_source_retention` policy-judgment tests + D2 증빙.

Spec: ADR-2026-09-26-B Decision 2 (SRC-1), policy-judgment slice only --
encryption-key management and the at-rest encrypted-column implementation
for `SOURCE_STORED` are OUT OF SCOPE for this leaf and land in a
follow-up tier-S leaf. 완료 하한: ADR-2026-09-09-C D2 -- negative >=3,
실패주입 1, 성능 단언 1, 게이트 적색 재현 1 (MP는 D3 축이 아님).

`validate_source_retention`은 I/O 없는 순수 함수라 allocation/domain/
policy.py, 그리고 이 패키지의 test_visibility.py 선례와 동일하게
"실패주입"은 두 문자열 리터럴 어디에도 속하지 않는 오염된 등급 값이
조용히 허용되지 않고 fail-closed로 거부되는지를 뜻하고, "게이트 적색
재현"은 그 거부가 반복 호출에도 뒤집히지 않는지를 뜻한다.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any

import pytest

from src.foundation.marketplace.contracts.v1 import ListingVisibility, MarketplaceErrorCode
from src.foundation.marketplace.domain.visibility import (
    validate_source_retention,
    validate_visibility_grade,
)


def _call_untyped(fn: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
    """negative test에서 Literal 경계 밖의 값을 의도적으로 넘길 때
    per-call `# type: ignore`(예산 소모) 대신 이 Any 경유 헬퍼를 쓴다 --
    tests/unit/foundation/marketplace/test_visibility.py의 선례와 동일 패턴."""
    return fn(*args, **kwargs)


# ---- negative >= 3 (DoD #1: PROTECTED/INVITE/PRIVATE + SOURCE_STORED) ----


def test_protected_source_stored_rejected():
    with pytest.raises(ValueError, match=MarketplaceErrorCode.VISIBILITY_DENIED.value):
        validate_source_retention(ListingVisibility.PROTECTED, "SOURCE_STORED")


def test_invite_source_stored_rejected():
    with pytest.raises(ValueError, match=MarketplaceErrorCode.VISIBILITY_DENIED.value):
        validate_source_retention(ListingVisibility.INVITE, "SOURCE_STORED")


def test_private_source_stored_rejected():
    with pytest.raises(ValueError, match=MarketplaceErrorCode.VISIBILITY_DENIED.value):
        validate_source_retention(ListingVisibility.PRIVATE, "SOURCE_STORED")


# ---- DoD #2: PUBLIC + SOURCE_STORED is allowed ----


def test_public_source_stored_allowed():
    validate_source_retention(ListingVisibility.PUBLIC, "SOURCE_STORED")  # no exception


def test_default_retention_is_ir_only_and_allowed_everywhere():
    # 기본값(IR_ONLY)은 인자를 생략해도 4단계 전부에서 허용된다.
    for grade in (
        ListingVisibility.PUBLIC,
        ListingVisibility.PROTECTED,
        ListingVisibility.INVITE,
        ListingVisibility.PRIVATE,
    ):
        validate_source_retention(grade)  # no exception


# ---- DoD #3: no regression against validate_visibility_grade ----


def test_has_protected_source_public_rejection_unaffected_by_retention():
    # has_protected_source=True 리스팅의 기존 PUBLIC 거부 로직은
    # source_retention과 완전히 독립적이다 -- 어느 쪽 retention 값을
    # 넘겨도 validate_visibility_grade의 거부 판정은 그대로다.
    with pytest.raises(ValueError, match=MarketplaceErrorCode.VISIBILITY_DENIED.value):
        validate_visibility_grade(ListingVisibility.PUBLIC, has_protected_source=True)
    # validate_source_retention 자체는 PUBLIC+아무 retention이나 허용 --
    # 두 검증기가 서로 충돌하지 않고 각자의 축만 판정한다.
    validate_source_retention(ListingVisibility.PUBLIC, "IR_ONLY")
    validate_source_retention(ListingVisibility.PUBLIC, "SOURCE_STORED")


def test_protected_with_protected_source_and_ir_only_passes_both_validators():
    # PROTECTED + has_protected_source=True + retention=IR_ONLY(기본값)는
    # 두 검증기 모두를 통과해야 한다 -- 회귀 없음의 양성 대조.
    validate_visibility_grade(ListingVisibility.PROTECTED, has_protected_source=True)
    validate_source_retention(ListingVisibility.PROTECTED)


# ---- 실패주입 1 + 게이트 적색 재현 1 ----


def test_unknown_retention_value_fails_closed():
    # 실패주입: 두 리터럴("IR_ONLY"/"SOURCE_STORED") 어디에도 속하지 않는
    # 오염된 값이 조용히 허용되지 않는지 확인 (fail-closed).
    with pytest.raises(ValueError, match=MarketplaceErrorCode.VISIBILITY_DENIED.value):
        _call_untyped(validate_source_retention, ListingVisibility.PUBLIC, "DELETE_AFTER_RUN")


def test_corrupted_retention_fails_closed_and_reproduces_on_repeat():
    # 실패주입: 문자열이 아닌 오염된 등급 값을 주입해도 PROTECTED 리스팅에
    # 원본 소스 영구 보관이 허용되지 않는지 확인.
    class _CorruptedRetention:
        pass

    corrupted = _CorruptedRetention()

    def _attempt() -> None:
        with pytest.raises(ValueError, match=MarketplaceErrorCode.VISIBILITY_DENIED.value):
            _call_untyped(validate_source_retention, ListingVisibility.PROTECTED, corrupted)

    # 게이트 적색 재현: 동일 오염 입력을 반복 호출해도 거부 판정이
    # 뒤집히지 않는다(우연한 1회성 통과가 아님을 증명).
    for _ in range(3):
        _attempt()


# ---- 수치 성능 단언 ----
# validate_source_retention은 I/O 없는 순수 함수 모듈이라 실패주입/게이트
# 재현/D3(적대적·리플레이·동시성) 증거는 구조적으로 제한적이다
# (test_visibility.py 선례와 동일 사유). 남는 성능 단언을 hot path에 건다.


@pytest.mark.perf
def test_validate_source_retention_hot_path_performance():
    start = time.perf_counter()
    for _ in range(10_000):
        validate_source_retention(ListingVisibility.PROTECTED, "IR_ONLY")
    elapsed = time.perf_counter() - start
    assert elapsed < 1.0
