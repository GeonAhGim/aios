"""FA-1 — 엔티티 계층 불변조건(순수 함수, I/O·asyncpg 금지).

Spec: docs/specs/L4_ibor_fund_accounting_and_resilience_v1.0.md#FA-1 (§2.1·§4 FA-A1).

세 불변조건을 강제한다:
1. **상위 없는 하위 금지** — 부모가 없거나(`None`) 이미 폐쇄된 부모 아래에는
   새 하위 엔티티를 만들 수 없다(`require_open_parent`류 함수).
2. **통화 상속** — Portfolio/SubAccount는 자기 통화 필드가 없다(계약 계층에서
   구조적으로 강제). 다른 바운디드 컨텍스트(원장 등)가 어떤 통화로 기표하려는
   시점에 그 통화가 소속 Fund의 `base_currency`와 일치하는지 확인하는 지점이
   `validate_currency_matches_fund`다.
3. **폐쇄 규칙** — 활성(`closed_at is None`) 하위가 하나라도 남아 있으면 상위를
   폐쇄할 수 없고, 이미 폐쇄된 엔티티는 재폐쇄할 수 없다.

호출자(조회/영속화)는 `adapters/postgres_repository.py`(FA-2)의 책임이다 —
이 모듈은 이미 로드된 객체(또는 부재를 뜻하는 `None`)만 받는다.
"""
from __future__ import annotations

from collections.abc import Iterable
from typing import ClassVar

from src.data.models.base import Currency
from src.foundation.entities.contracts.v1 import (
    EntityErrorCode,
    Fund,
    LegalEntity,
    Portfolio,
    SubAccount,
)


class HierarchyViolationError(ValueError):
    """FA_HIERARCHY_VIOLATION(400) — 상위 없음/폐쇄된 상위 아래 생성 시도/통화 불일치."""

    code: ClassVar[EntityErrorCode] = EntityErrorCode.HIERARCHY_VIOLATION


class AlreadyClosedError(ValueError):
    """FA_ALREADY_CLOSED(409) — 이미 폐쇄된 엔티티를 다시 폐쇄하려는 시도."""

    code: ClassVar[EntityErrorCode] = EntityErrorCode.ALREADY_CLOSED


class CloseBlockedByChildError(ValueError):
    """FA_CLOSE_BLOCKED_BY_CHILD(409) — 활성 하위가 남아 있어 폐쇄 불가."""

    code: ClassVar[EntityErrorCode] = EntityErrorCode.CLOSE_BLOCKED_BY_CHILD


def _require_open_parent(
    parent: LegalEntity | Fund | Portfolio | None,
    *,
    parent_kind: str,
    child_kind: str,
) -> None:
    if parent is None:
        raise HierarchyViolationError(
            f"{child_kind}은(는) 존재하지 않는 {parent_kind}를 참조할 수 없음"
        )
    if parent.closed_at is not None:
        raise HierarchyViolationError(
            f"{child_kind}은(는) 폐쇄된 {parent_kind}({parent.closed_at}) 아래에 생성할 수 없음"
        )


def validate_new_fund(entity: LegalEntity | None) -> None:
    """Fund 생성 전 호출 — `entity`는 조회 결과(없으면 `None`)."""
    _require_open_parent(entity, parent_kind="LegalEntity", child_kind="Fund")


def validate_new_portfolio(fund: Fund | None) -> None:
    _require_open_parent(fund, parent_kind="Fund", child_kind="Portfolio")


def validate_new_sub_account(portfolio: Portfolio | None) -> None:
    _require_open_parent(portfolio, parent_kind="Portfolio", child_kind="SubAccount")


def validate_currency_matches_fund(fund: Fund, currency: Currency) -> None:
    """원장/포지션 기표 등 다른 컨텍스트가 쓰려는 통화가 상속 규칙을
    지키는지 검사한다. Portfolio/SubAccount 계약에 통화 필드가 없으므로
    이 함수가 유일한 강제 지점이다."""
    if currency != fund.base_currency:
        raise HierarchyViolationError(
            f"통화 {currency.value}는 Fund {fund.fund_id}의 base_currency "
            f"{fund.base_currency.value}를 따르지 않음(통화 상속 위반)"
        )


def validate_close_entity(entity: LegalEntity, funds: Iterable[Fund]) -> None:
    """LegalEntity 폐쇄 전 호출. `funds`는 이 entity 소속 여부를 이미
    걸러왔든 안 걸러왔든 상관없다 — 여기서 `entity_id`로 다시 거른다."""
    if entity.closed_at is not None:
        raise AlreadyClosedError(f"LegalEntity {entity.entity_id}는 이미 폐쇄됨")
    active = [f for f in funds if f.entity_id == entity.entity_id and f.closed_at is None]
    if active:
        raise CloseBlockedByChildError(
            f"LegalEntity {entity.entity_id} 폐쇄 불가 — 활성 Fund {len(active)}개 존재"
        )


def validate_close_fund(fund: Fund, portfolios: Iterable[Portfolio]) -> None:
    if fund.closed_at is not None:
        raise AlreadyClosedError(f"Fund {fund.fund_id}는 이미 폐쇄됨")
    active = [p for p in portfolios if p.fund_id == fund.fund_id and p.closed_at is None]
    if active:
        raise CloseBlockedByChildError(
            f"Fund {fund.fund_id} 폐쇄 불가 — 활성 Portfolio {len(active)}개 존재"
        )


def validate_close_portfolio(portfolio: Portfolio, sub_accounts: Iterable[SubAccount]) -> None:
    if portfolio.closed_at is not None:
        raise AlreadyClosedError(f"Portfolio {portfolio.portfolio_id}는 이미 폐쇄됨")
    active = [
        s
        for s in sub_accounts
        if s.portfolio_id == portfolio.portfolio_id and s.closed_at is None
    ]
    if active:
        raise CloseBlockedByChildError(
            f"Portfolio {portfolio.portfolio_id} 폐쇄 불가 — 활성 SubAccount {len(active)}개 존재"
        )


def validate_close_sub_account(sub_account: SubAccount) -> None:
    """SubAccount는 최하위라 하위 검사가 없다 — 재폐쇄만 막는다."""
    if sub_account.closed_at is not None:
        raise AlreadyClosedError(f"SubAccount {sub_account.sub_account_id}는 이미 폐쇄됨")
