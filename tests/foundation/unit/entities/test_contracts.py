"""FA-1 contracts/v1.py — 계약 레벨 검증(schema_version, tz-aware 강제)."""

from __future__ import annotations

import uuid
from datetime import date, datetime
from uuid import uuid4

import pytest
from pydantic import ValidationError

from src.data.models.base import Currency
from src.foundation.entities.contracts.v1 import (
    EntityContext,
    Fund,
    LegalEntity,
    Portfolio,
    SubAccount,
)


def test_legal_entity_defaults_schema_version_and_open():
    entity = LegalEntity(
        entity_id=uuid4(),
        tenant_id=uuid4(),
        name="Acme Capital",
        jurisdiction="KR",
        region_tag="ap-northeast-2",
    )
    assert entity.schema_version == "v1"
    assert entity.closed_at is None


def test_fund_mandate_ref_optional():
    fund = Fund(
        fund_id=uuid4(),
        entity_id=uuid4(),
        base_currency=Currency.KRW,
        inception=date(2026, 1, 1),
    )
    assert fund.mandate_ref is None


def test_portfolio_and_sub_account_have_no_currency_field():
    """통화는 Fund에만 있다 — 상속은 필드 부재로 구조적으로 강제된다."""
    portfolio = Portfolio(portfolio_id=uuid4(), fund_id=uuid4(), venue_account_ref="ACC-1")
    sub_account = SubAccount(sub_account_id=uuid4(), portfolio_id=uuid4(), owner_ref=uuid4())
    assert not hasattr(portfolio, "base_currency")
    assert not hasattr(sub_account, "base_currency")


def test_closed_at_rejects_naive_datetime():
    """AwareDatetime은 tz-naive 값을 거부한다(LC-1/LB-1과 동일 관례) — negative."""
    with pytest.raises(ValidationError):
        LegalEntity(
            entity_id=uuid4(),
            tenant_id=uuid4(),
            name="Acme Capital",
            jurisdiction="KR",
            region_tag="ap-northeast-2",
            closed_at=datetime(2026, 1, 1),  # naive, tz 없음
        )


def test_fund_rejects_unknown_currency():
    with pytest.raises(ValidationError):
        Fund(
            fund_id=uuid4(),
            entity_id=uuid4(),
            base_currency="EUR",  # Currency enum에 없는 값
            inception=date(2026, 1, 1),
        )


def test_entity_context_rejects_missing_required_field():
    """EntityContext의 5개 필드는 전부 필수다 — 부분 해석 상태는 없다(§FA-5) — negative."""
    with pytest.raises(ValidationError):
        EntityContext(
            tenant_id=uuid4(),
            legal_entity_id=uuid4(),
            fund_id=uuid4(),
            portfolio_id=uuid4(),
            # sub_account_id 누락
        )


def test_portfolio_rejects_non_uuid_fund_id():
    """fund_id는 UUID 형식만 허용한다 — 임의 문자열은 구조적으로 거부된다 — negative."""
    with pytest.raises(ValidationError):
        Portfolio(
            portfolio_id=uuid4(),
            fund_id="not-a-uuid",
            venue_account_ref="ACC-1",
        )


def test_legal_entity_uuid_parsing_dependency_failure_propagates(monkeypatch):
    """uuid.UUID 파싱 의존성이 예외를 던지면 LegalEntity 생성도 예외로 전파된다
    (fail-closed — 값을 추측해 성공으로 위장하지 않는다) — 실패주입."""

    def _boom(*args, **kwargs):
        raise RuntimeError("uuid backend down")

    monkeypatch.setattr(uuid, "UUID", _boom)

    with pytest.raises(RuntimeError):
        LegalEntity(
            entity_id="11111111-1111-1111-1111-111111111111",
            tenant_id=uuid4(),
            name="Acme Capital",
            jurisdiction="KR",
            region_tag="ap-northeast-2",
        )
