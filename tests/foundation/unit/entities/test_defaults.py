"""FA-1 domain/defaults.py — 개인 사용자 기본 계층 결정론 검증."""
from __future__ import annotations

from datetime import date
from uuid import uuid4

from src.data.models.base import Currency
from src.foundation.entities.domain.defaults import (
    build_default_hierarchy,
    default_entity_id,
    default_fund_id,
    default_portfolio_id,
    default_sub_account_id,
)

_KWARGS = dict(
    base_currency=Currency.KRW,
    jurisdiction="KR",
    region_tag="ap-northeast-2",
    venue_account_ref="ACC-1",
    inception=date(2026, 1, 1),
)


def test_default_ids_are_deterministic_for_same_user():
    user_id = uuid4()
    assert default_entity_id(user_id) == default_entity_id(user_id)
    assert default_fund_id(user_id) == default_fund_id(user_id)
    assert default_portfolio_id(user_id) == default_portfolio_id(user_id)
    assert default_sub_account_id(user_id) == default_sub_account_id(user_id)


def test_default_ids_differ_across_users():
    a, b = uuid4(), uuid4()
    assert default_entity_id(a) != default_entity_id(b)
    assert default_fund_id(a) != default_fund_id(b)


def test_default_ids_differ_across_levels_for_same_user():
    """같은 user_id라도 이산자가 달라 4개 id가 전부 달라야 한다 —
    안 그러면 entity_id와 fund_id가 우연히 같아져 FA-2 FK가 뒤엉킨다."""
    user_id = uuid4()
    ids = {
        default_entity_id(user_id),
        default_fund_id(user_id),
        default_portfolio_id(user_id),
        default_sub_account_id(user_id),
    }
    assert len(ids) == 4


def test_build_default_hierarchy_links_correctly():
    user_id = uuid4()
    tenant_id = uuid4()
    hierarchy = build_default_hierarchy(user_id=user_id, tenant_id=tenant_id, **_KWARGS)

    assert hierarchy.legal_entity.entity_id == default_entity_id(user_id)
    assert hierarchy.legal_entity.tenant_id == tenant_id
    assert hierarchy.fund.entity_id == hierarchy.legal_entity.entity_id
    assert hierarchy.portfolio.fund_id == hierarchy.fund.fund_id
    assert hierarchy.sub_account.portfolio_id == hierarchy.portfolio.portfolio_id
    assert hierarchy.sub_account.owner_ref == user_id
    assert all(
        e.closed_at is None
        for e in (
            hierarchy.legal_entity,
            hierarchy.fund,
            hierarchy.portfolio,
            hierarchy.sub_account,
        )
    )


def test_build_default_hierarchy_is_idempotent_across_calls():
    """같은 사용자에 대해 두 번 호출해도 같은 id 트리를 재현한다(FA-3
    백필이 조회 없이 재계산해도 안전해야 한다는 요구의 핵심)."""
    user_id = uuid4()
    tenant_id = uuid4()
    first = build_default_hierarchy(user_id=user_id, tenant_id=tenant_id, **_KWARGS)
    second = build_default_hierarchy(user_id=user_id, tenant_id=tenant_id, **_KWARGS)

    assert first.legal_entity.entity_id == second.legal_entity.entity_id
    assert first.fund.fund_id == second.fund.fund_id
    assert first.portfolio.portfolio_id == second.portfolio.portfolio_id
    assert first.sub_account.sub_account_id == second.sub_account.sub_account_id
