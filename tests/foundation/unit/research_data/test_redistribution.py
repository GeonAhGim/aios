"""RD-3 — `domain/redistribution.assert_redistribution_allowed` tests.

Spec: docs/specs/L4_research_data_and_market_ecosystem_v1.0.md §3, §9 RD-3
DoD (a)(b).
"""
from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

import pytest

from src.foundation.market_data.domain.entitlement import source_contract as source_contract_module
from src.foundation.market_data.domain.entitlement.source_contract import (
    RedistributionScope,
    SourceCapability,
    SourceContractGrant,
    SourceContractTier,
)
from src.foundation.research_data.contracts.v1 import ResearchItem, SourceMeta
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


def _source(*, source_id: str = "GDELT", redistribution: str = "link_only") -> SourceMeta:
    return SourceMeta(
        source_id=source_id,
        publisher="GDELT Project",
        redistribution=redistribution,  # type: ignore[arg-type]
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
