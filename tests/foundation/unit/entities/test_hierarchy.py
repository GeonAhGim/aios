"""FA-1 domain/hierarchy.py — 계층 불변조건 단위테스트(DB 없이 순수 함수만)."""
from __future__ import annotations

from datetime import date, datetime, timezone
from uuid import uuid4

import pytest

from src.data.models.base import Currency
from src.foundation.entities.contracts.v1 import Fund, LegalEntity, Portfolio, SubAccount
from src.foundation.entities.domain.hierarchy import (
    AlreadyClosedError,
    CloseBlockedByChildError,
    HierarchyViolationError,
    validate_close_entity,
    validate_close_fund,
    validate_close_portfolio,
    validate_close_sub_account,
    validate_currency_matches_fund,
    validate_new_fund,
    validate_new_portfolio,
    validate_new_sub_account,
)

NOW = datetime(2026, 9, 6, tzinfo=timezone.utc)


def _entity(**overrides: object) -> LegalEntity:
    defaults: dict[str, object] = dict(
        entity_id=uuid4(), tenant_id=uuid4(), name="Acme", jurisdiction="KR", region_tag="ap-2"
    )
    defaults.update(overrides)
    return LegalEntity(**defaults)  # type: ignore[arg-type]


def _fund(entity_id, **overrides: object) -> Fund:
    defaults: dict[str, object] = dict(
        fund_id=uuid4(),
        entity_id=entity_id,
        base_currency=Currency.KRW,
        inception=date(2026, 1, 1),
    )
    defaults.update(overrides)
    return Fund(**defaults)  # type: ignore[arg-type]


def _portfolio(fund_id, **overrides: object) -> Portfolio:
    defaults: dict[str, object] = dict(
        portfolio_id=uuid4(), fund_id=fund_id, venue_account_ref="ACC-1"
    )
    defaults.update(overrides)
    return Portfolio(**defaults)  # type: ignore[arg-type]


def _sub_account(portfolio_id, **overrides: object) -> SubAccount:
    defaults: dict[str, object] = dict(
        sub_account_id=uuid4(), portfolio_id=portfolio_id, owner_ref=uuid4()
    )
    defaults.update(overrides)
    return SubAccount(**defaults)  # type: ignore[arg-type]


# ---- 상위 없는 하위 금지 ----


def test_validate_new_fund_rejects_missing_parent():
    with pytest.raises(HierarchyViolationError):
        validate_new_fund(None)


def test_validate_new_fund_rejects_closed_parent():
    entity = _entity(closed_at=NOW)
    with pytest.raises(HierarchyViolationError):
        validate_new_fund(entity)


def test_validate_new_fund_allows_open_parent():
    entity = _entity()
    validate_new_fund(entity)  # raises 없음


def test_validate_new_portfolio_rejects_missing_parent():
    with pytest.raises(HierarchyViolationError):
        validate_new_portfolio(None)


def test_validate_new_sub_account_rejects_closed_parent():
    portfolio = _portfolio(fund_id=uuid4(), closed_at=NOW)
    with pytest.raises(HierarchyViolationError):
        validate_new_sub_account(portfolio)


# ---- 통화 상속 ----


def test_currency_matches_fund_passes():
    fund = _fund(uuid4(), base_currency=Currency.KRW)
    validate_currency_matches_fund(fund, Currency.KRW)  # raises 없음


def test_currency_mismatch_raises():
    fund = _fund(uuid4(), base_currency=Currency.KRW)
    with pytest.raises(HierarchyViolationError):
        validate_currency_matches_fund(fund, Currency.USDT)


# ---- 폐쇄 규칙 ----


def test_close_entity_blocked_by_active_fund():
    entity = _entity()
    active_fund = _fund(entity.entity_id)
    with pytest.raises(CloseBlockedByChildError):
        validate_close_entity(entity, [active_fund])


def test_close_entity_succeeds_when_all_funds_closed():
    entity = _entity()
    closed_fund = _fund(entity.entity_id, closed_at=NOW)
    validate_close_entity(entity, [closed_fund])  # raises 없음


def test_close_entity_rejects_already_closed():
    entity = _entity(closed_at=NOW)
    with pytest.raises(AlreadyClosedError):
        validate_close_entity(entity, [])


def test_close_fund_blocked_by_active_portfolio():
    fund = _fund(uuid4())
    active_portfolio = _portfolio(fund.fund_id)
    with pytest.raises(CloseBlockedByChildError):
        validate_close_fund(fund, [active_portfolio])


def test_close_portfolio_blocked_by_active_sub_account():
    portfolio = _portfolio(uuid4())
    active_sub_account = _sub_account(portfolio.portfolio_id)
    with pytest.raises(CloseBlockedByChildError):
        validate_close_portfolio(portfolio, [active_sub_account])


def test_close_portfolio_ignores_unrelated_sub_accounts():
    portfolio = _portfolio(uuid4())
    unrelated = _sub_account(uuid4())
    validate_close_portfolio(portfolio, [unrelated])  # raises 없음


def test_close_sub_account_rejects_already_closed():
    sub_account = _sub_account(uuid4(), closed_at=NOW)
    with pytest.raises(AlreadyClosedError):
        validate_close_sub_account(sub_account)


def test_close_sub_account_succeeds_when_open():
    sub_account = _sub_account(uuid4())
    validate_close_sub_account(sub_account)  # raises 없음
