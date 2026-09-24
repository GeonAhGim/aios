"""MP-2 -- marketplace/domain/visibility 4단계 가시성 규칙 + D2 증빙.

Spec: docs/specs/L4_analytics_authoring_backtest_marketplace_v1.0.md §9.7(MP)
MP-2. 완료 하한: ADR-2026-09-09-C D2 -- negative >=3, 실패주입 1, 성능
단언 1, 게이트 적색 재현 1 (MP는 D3 축(R/L4/LA-LC/FA/CM/EO/DC)이 아님).

`visibility.py`는 I/O 없는 순수 함수라 allocation/domain/policy.py 선례와
동일하게 "실패주입"은 pydantic 경계를 넘어온 오염된(enum이 아닌) 등급 값이
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
    ViewerContext,
    VisibilityDecision,
    resolve_visibility,
    validate_visibility_grade,
)

OWNER = ViewerContext(is_owner=True)
STRANGER = ViewerContext()
INVITEE = ViewerContext(is_invited=True)


def _call_untyped(fn: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
    """negative test에서 pydantic/enum 경계 밖의 값을 의도적으로 넘길 때
    per-call `# type: ignore`(PLT-40 예산 소모) 대신 이 Any 경유 헬퍼를 쓴다
    -- tests/unit/api/schemas/test_marketplace.py의 선례와 동일 패턴."""
    return fn(*args, **kwargs)


# ---- 4단계 판정 (positive) ----


def test_public_visible_to_everyone():
    decision = resolve_visibility(
        ListingVisibility.PUBLIC, has_protected_source=False, viewer=STRANGER
    )
    assert decision == VisibilityDecision(listing_visible=True, source_visible=True)


def test_protected_listing_discoverable_but_source_hidden_from_stranger():
    decision = resolve_visibility(
        ListingVisibility.PROTECTED, has_protected_source=False, viewer=STRANGER
    )
    assert decision == VisibilityDecision(listing_visible=True, source_visible=False)


def test_protected_source_hidden_even_from_invited_viewer():
    # protected 소스 여부와 무관하게, PROTECTED 등급 자체는 owner가 아닌
    # 누구에게도 소스를 노출하지 않는다 -- invited라도 예외 없음.
    decision = resolve_visibility(
        ListingVisibility.PROTECTED, has_protected_source=False, viewer=INVITEE
    )
    assert decision.source_visible is False


def test_invite_visible_to_invited_viewer():
    decision = resolve_visibility(
        ListingVisibility.INVITE, has_protected_source=False, viewer=INVITEE
    )
    assert decision == VisibilityDecision(listing_visible=True, source_visible=True)


def test_invite_hidden_from_uninvited_stranger():
    decision = resolve_visibility(
        ListingVisibility.INVITE, has_protected_source=False, viewer=STRANGER
    )
    assert decision == VisibilityDecision(listing_visible=False, source_visible=False)


def test_private_visible_only_to_owner():
    stranger_decision = resolve_visibility(
        ListingVisibility.PRIVATE, has_protected_source=False, viewer=STRANGER
    )
    owner_decision = resolve_visibility(
        ListingVisibility.PRIVATE, has_protected_source=False, viewer=OWNER
    )
    assert stranger_decision == VisibilityDecision(listing_visible=False, source_visible=False)
    assert owner_decision == VisibilityDecision(listing_visible=True, source_visible=True)


def test_owner_always_sees_source_regardless_of_grade():
    for grade in (
        ListingVisibility.PROTECTED,
        ListingVisibility.INVITE,
        ListingVisibility.PRIVATE,
    ):
        decision = resolve_visibility(grade, has_protected_source=False, viewer=OWNER)
        assert decision == VisibilityDecision(listing_visible=True, source_visible=True)


# ---- negative >= 3 ----


def test_protected_source_bypass_via_public_request_is_rejected():
    # 협의된 거부 입력: has_protected_source=True 인데 PUBLIC 등급을
    # 요청하면 ValueError -- owner가 요청해도 예외 없이 거부된다.
    with pytest.raises(ValueError, match=MarketplaceErrorCode.VISIBILITY_DENIED.value):
        resolve_visibility(
            ListingVisibility.PUBLIC, has_protected_source=True, viewer=OWNER
        )
    with pytest.raises(ValueError, match=MarketplaceErrorCode.VISIBILITY_DENIED.value):
        validate_visibility_grade(ListingVisibility.PUBLIC, has_protected_source=True)


def test_protected_source_allowed_at_non_public_grades():
    # 위 거부가 과도하게 넓지 않은지 확인 -- PUBLIC이 아닌 나머지 3단계는
    # has_protected_source=True 여도 구조적으로 문제 없다(소스를 임의
    # 열람자에게 노출하는 등급이 아니므로).
    for grade in (
        ListingVisibility.PROTECTED,
        ListingVisibility.INVITE,
        ListingVisibility.PRIVATE,
    ):
        validate_visibility_grade(grade, has_protected_source=True)  # 예외 없음


def test_missing_grade_is_rejected():
    with pytest.raises(ValueError, match=MarketplaceErrorCode.VISIBILITY_DENIED.value):
        _call_untyped(resolve_visibility, None, has_protected_source=False, viewer=STRANGER)


def test_invalid_grade_value_is_rejected_fail_closed():
    # 잘못된 등급 전이: pydantic 경계 밖에서 유입된, 4단계 어디에도
    # 속하지 않는 값 -- allow-list로 처리되므로 fail-closed로 거부된다
    # (fail-open으로 조용히 허용되는 회귀를 잡는다).
    class _BogusGrade:
        def __repr__(self) -> str:
            return "<bogus>"

    with pytest.raises(ValueError, match=MarketplaceErrorCode.VISIBILITY_DENIED.value):
        _call_untyped(
            resolve_visibility, _BogusGrade(), has_protected_source=False, viewer=STRANGER
        )


# ---- 실패주입 1 + 게이트 적색 재현 1 ----


def test_corrupted_grade_fails_closed_and_reproduces_on_repeat():
    # 실패주입: enum이 아닌 오염된 등급 값을 주입해도 STRANGER에게 소스가
    # 새어나가지 않는지 확인.
    class _CorruptedGrade:
        pass

    corrupted = _CorruptedGrade()

    def _attempt() -> None:
        with pytest.raises(ValueError, match=MarketplaceErrorCode.VISIBILITY_DENIED.value):
            _call_untyped(
                resolve_visibility, corrupted, has_protected_source=False, viewer=STRANGER
            )

    # 게이트 적색 재현: 동일 오염 입력을 반복 호출해도 거부 판정이
    # 뒤집히지 않는다(우연한 1회성 통과가 아님을 증명).
    for _ in range(3):
        _attempt()


# ---- 수치 성능 단언 ----
# visibility.py는 I/O 없는 순수 함수 모듈이라 실패주입/게이트재현/D3(적대적·
# 리플레이·동시성) 증거는 구조적으로 제한적이다(allocation/domain/policy.py
# 선례와 동일 사유). 남는 성능 단언을 hot path에 건다.


def test_resolve_visibility_hot_path_performance():
    start = time.perf_counter()
    for _ in range(10_000):
        resolve_visibility(ListingVisibility.PROTECTED, has_protected_source=False, viewer=STRANGER)
    elapsed = time.perf_counter() - start
    assert elapsed < 1.0
