"""RD-3 — `domain/redistribution.assert_redistribution_allowed` tests.

Spec: docs/specs/L4_research_data_and_market_ecosystem_v1.0.md §3, §9 RD-3
DoD (a)(b).
"""

from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import cast
from uuid import uuid4

import pytest

from src.foundation.market_data.domain.entitlement import source_contract as source_contract_module
from src.foundation.market_data.domain.entitlement.source_contract import (
    DataUse,
    RedistributionScope,
    SourceCapability,
    SourceContractGrant,
    SourceContractTier,
)
from src.foundation.research_data.contracts.v1 import (
    RedistributionPolicy,
    ResearchItem,
    SourceMeta,
)
from src.foundation.research_data.domain.redistribution import (
    RedistributionViolationError,
    assert_redistribution_allowed,
)

_KNOWN_AT = datetime(2026, 9, 8, 12, 0, tzinfo=timezone.utc)
_CAPABILITY = SourceCapability(
    asset_classes=frozenset({"EQUITY_KR"}), resolutions=frozenset({"1d"})
)


def _item(*, source_id: str = "GDELT", body_ref: str | None) -> ResearchItem:
    return ResearchItem(
        item_id=uuid4(),
        source_id=source_id,
        kind="news",
        published_at=_KNOWN_AT,
        known_at=_KNOWN_AT,
        instruments=("005930",),
        title="some headline",
        body_ref=body_ref,
        url="https://example.com/x",
        language="en",
        hash="h" * 64,
        revision_of=None,
    )


def _source(
    *, source_id: str = "GDELT", redistribution: RedistributionPolicy = "link_only"
) -> SourceMeta:
    return SourceMeta(
        source_id=source_id,
        publisher="GDELT Project",
        redistribution=redistribution,
        license_ref="https://example.com/license",
        rate_limit=60,
        coverage="2015-01-01~present",
    )


def _grant(
    *, allowed: bool = True, scope: RedistributionScope | None = RedistributionScope.INTERNAL
) -> SourceContractGrant:
    if not allowed:
        return SourceContractGrant(
            allowed=False,
            tier=None,
            redistribution_scope=None,
            rate_limit=None,
            quota=None,
            capability=None,
            denial_reason=source_contract_module.SourceContractDenialReason.NOT_FOUND,
        )
    return SourceContractGrant(
        allowed=True,
        tier=SourceContractTier.FREE,
        redistribution_scope=scope,
        rate_limit=60,
        quota=1000,
        capability=_CAPABILITY,
        denial_reason=None,
    )


def test_link_only_source_with_one_char_body_rejected() -> None:
    """RD-3 DoD (a) — 본문 문자열 1자만 있어도 link_only 소스는 거부."""
    item = _item(body_ref="a")
    source = _source(redistribution="link_only")
    with pytest.raises(RedistributionViolationError) as exc_info:
        assert_redistribution_allowed(item, source, _grant())
    assert exc_info.value.error_code.value == "RD_REDISTRIBUTION_DENIED"
    assert exc_info.value.reason == "link_only_body_present"


def test_link_only_source_without_body_passes() -> None:
    item = _item(body_ref=None)
    source = _source(redistribution="link_only")
    assert_redistribution_allowed(item, source, _grant())  # raise 없음


def test_store_full_source_with_body_allowed_when_grant_permits() -> None:
    item = _item(body_ref="full article text")
    source = _source(redistribution="store_full")
    assert_redistribution_allowed(item, source, _grant(scope=RedistributionScope.REDISTRIBUTE))


def test_source_contract_denied_blocks_even_when_license_allows_body() -> None:
    """RD-3 DoD (b) — 등급 강제(DC-27)는 라이선스(store_full)와 독립적인
    별도 게이트다: 계약이 없거나 만료되면 store_full이어도 거부된다."""
    item = _item(body_ref="full article text")
    source = _source(redistribution="store_full")
    with pytest.raises(RedistributionViolationError) as exc_info:
        assert_redistribution_allowed(item, source, _grant(allowed=False))
    assert exc_info.value.reason == "source_contract_denied"


def test_none_scope_grant_blocks_even_when_license_allows_body() -> None:
    item = _item(body_ref="full article text")
    source = _source(redistribution="store_full")
    with pytest.raises(RedistributionViolationError):
        assert_redistribution_allowed(item, source, _grant(scope=RedistributionScope.NONE))


def test_mismatched_source_id_rejected() -> None:
    item = _item(source_id="GDELT", body_ref=None)
    source = _source(source_id="RSS_GENERIC")
    with pytest.raises(ValueError):
        assert_redistribution_allowed(item, source, _grant())


def test_delegates_to_permits_use_not_reimplemented(monkeypatch: pytest.MonkeyPatch) -> None:
    """RD-3 DoD (b) 재구현 금지 반증: `permits_use`를 monkeypatch로 뒤집으면
    판정이 그대로 따라 바뀌어야 한다 — 이 모듈이 자체 등급표를 다시
    짰다면 이 patch가 결과에 영향을 주지 못해 테스트가 실패한다."""
    import src.foundation.research_data.domain.redistribution as redistribution_module

    item = _item(body_ref="full article text")
    source = _source(redistribution="store_full")

    # 원래는 통과해야 하는 조합(REDISTRIBUTE)인데 permits_use가 항상 거부하도록
    # 바꾸면 위임하는 구현은 거부로 뒤집힌다.
    monkeypatch.setattr(redistribution_module, "permits_use", lambda scope, use: False)
    with pytest.raises(RedistributionViolationError):
        assert_redistribution_allowed(item, source, _grant(scope=RedistributionScope.REDISTRIBUTE))

    # 반대로 원래는 거부해야 하는 조합(NONE)인데 permits_use가 항상 허용하도록
    # 바꾸면 위임하는 구현은 통과로 뒤집힌다.
    monkeypatch.setattr(redistribution_module, "permits_use", lambda scope, use: True)
    assert_redistribution_allowed(item, source, _grant(scope=RedistributionScope.NONE))


# ---- DEEPEN(task-2907) — DEPTH_DC_RD.md D2 부족분: 실패주입/성능단언/게이트적색 ----


def test_corrupted_grant_scope_propagates_keyerror_instead_of_silently_permitting() -> None:
    """실패 주입 — DC-27 grant가 pydantic 검증을 우회해 만들어진 경우(예:
    성능 경로에서 `model_construct`를 쓰는 어댑터, 또는 구버전 enum 값이
    남은 행의 역직렬화) `redistribution_scope`가 이 코드가 아는
    `RedistributionScope` 멤버가 아닐 수 있다. `permits_use` 내부 딕셔너리
    조회는 이때 `KeyError`를 던지는데, `assert_redistribution_allowed`는
    이를 삼켜 조용히 허용으로 돌리지 않고 그대로 전파해야 한다 — 모르는
    상태를 허용으로 해석하는 것은 fail-open이라 금지된다."""
    item = _item(body_ref="full article text")
    source = _source(redistribution="store_full")
    bogus_scope = cast(RedistributionScope, "BOGUS_SCOPE")
    grant = SourceContractGrant.model_construct(
        allowed=True,
        tier=SourceContractTier.FREE,
        redistribution_scope=bogus_scope,
        rate_limit=60,
        quota=1000,
        capability=_CAPABILITY,
        denial_reason=None,
    )
    with pytest.raises(KeyError):
        assert_redistribution_allowed(item, source, grant)


@pytest.mark.perf
def test_assert_redistribution_allowed_meets_latency_budget_for_bulk_calls() -> None:
    """성능 단언 — 순수 함수(I/O 없음) 호출은 상수 시간이어야 한다. 대량
    반복 호출(예: 배치 수집 후 일괄 검증)이 절대시간 예산 내에 있다 —
    회귀가 있다면(예: 매 호출마다 소스 테이블을 O(n) 스캔하는 재구현으로
    퇴화) 예산을 넘는다."""
    item = _item(body_ref=None)
    source = _source(redistribution="link_only")
    grant = _grant()

    budget_sec = 1.0  # 실측 로컬 <0.05s(20,000회, 상수시간 순수 함수 기준)
    start_time = time.perf_counter()
    for _ in range(20_000):
        assert_redistribution_allowed(item, source, grant)
    elapsed = time.perf_counter() - start_time

    print(f"[RD-3 redistribution] 20000 calls in {elapsed:.3f}s (budget<{budget_sec}s)")
    assert elapsed < budget_sec, (
        f"assert_redistribution_allowed 20000회 호출이 예산({budget_sec}s)을 "
        f"넘었습니다({elapsed:.3f}s) — 상수시간 순수 함수가 아닌 무언가로 "
        "퇴화했는지 확인하세요."
    )


def test_gate_red_if_license_gate_removed_existing_negative_would_flip() -> None:
    """게이트 적색 재현 — RD-3(a) 라이선스 게이트(link_only + body_ref
    거부) 줄이 실수로 삭제된 회귀를 흉내낸 대조 구현을 구성해, 그 버전은
    동일 입력을 거부하지 않고 조용히 통과시킴(green→red 반전)을 실측한다.
    현재 구현은 동일 입력을 여전히 거부한다 — 이 테스트가 실제로 위험한
    회귀를 잡아낼 수 있다는 증거다."""
    item = _item(body_ref="a")
    source = _source(redistribution="link_only")
    grant = _grant()

    def _regressed_without_license_gate(
        item: ResearchItem, source: SourceMeta, grant: SourceContractGrant
    ) -> None:
        # RD-3(a) 라이선스 게이트가 빠진 회귀 -- 등급 게이트만 남았다고 가정.
        if (
            not grant.allowed
            or grant.redistribution_scope is None
            or not source_contract_module.permits_use(
                grant.redistribution_scope, DataUse.INTERNAL_CALC
            )
        ):
            raise RedistributionViolationError(item, reason="source_contract_denied")
        # (license gate missing here -- link_only + body_ref 조합을 걸러내지 못한다)

    _regressed_without_license_gate(item, source, grant)  # raise 없음 == 회귀가 실제로 위험함

    with pytest.raises(RedistributionViolationError) as exc_info:
        assert_redistribution_allowed(item, source, grant)
    assert exc_info.value.reason == "link_only_body_present"
