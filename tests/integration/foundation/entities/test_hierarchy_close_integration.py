"""FA-2 — domain/hierarchy.py(FA-1) 순수 규칙 + PostgresEntityRepository 통합.

Spec: docs/specs/L4_ibor_fund_accounting_and_resilience_v1.0.md#FA-1 §4
"폐쇄 규칙". 활성 하위가 남은 상위는 도메인 계층이 폐쇄를 거부해야
하고, 하위부터 순서대로 폐쇄하면 실DB 상태에도 정확히 반영돼야 한다."""
from __future__ import annotations

import pytest

from src.foundation.entities.domain.hierarchy import (
    CloseBlockedByChildError,
    validate_close_entity,
    validate_close_fund,
    validate_close_portfolio,
    validate_close_sub_account,
)
from tests.integration.foundation.entities.conftest import build_hierarchy, now_utc


async def test_close_blocked_while_active_children_exist_then_succeeds_bottom_up(pool, repo):
    seeded = await build_hierarchy(pool, repo)

    funds = await repo.list_funds_by_entity(seeded.legal_entity.entity_id)
    with pytest.raises(CloseBlockedByChildError):
        validate_close_entity(seeded.legal_entity, funds)

    portfolios = await repo.list_portfolios_by_fund(seeded.fund.fund_id)
    with pytest.raises(CloseBlockedByChildError):
        validate_close_fund(seeded.fund, portfolios)

    sub_accounts = await repo.list_sub_accounts_by_portfolio(seeded.portfolio.portfolio_id)
    with pytest.raises(CloseBlockedByChildError):
        validate_close_portfolio(seeded.portfolio, sub_accounts)

    # 최하위부터 순서대로 폐쇄 — 각 단계에서 도메인 검증 통과 후 영속화.
    validate_close_sub_account(seeded.sub_account)
    await repo.close_sub_account(
        seeded.tenant_id, seeded.sub_account.sub_account_id, closed_at=now_utc()
    )

    portfolios_after = await repo.list_sub_accounts_by_portfolio(seeded.portfolio.portfolio_id)
    validate_close_portfolio(seeded.portfolio, portfolios_after)
    await repo.close_portfolio(
        seeded.tenant_id, seeded.portfolio.portfolio_id, closed_at=now_utc()
    )

    funds_after = await repo.list_portfolios_by_fund(seeded.fund.fund_id)
    validate_close_fund(seeded.fund, funds_after)
    await repo.close_fund(seeded.tenant_id, seeded.fund.fund_id, closed_at=now_utc())

    entity_funds_after = await repo.list_funds_by_entity(seeded.legal_entity.entity_id)
    validate_close_entity(seeded.legal_entity, entity_funds_after)
    closed_entity = await repo.close_legal_entity(
        seeded.tenant_id, seeded.legal_entity.entity_id, closed_at=now_utc()
    )

    assert closed_entity.closed_at is not None
