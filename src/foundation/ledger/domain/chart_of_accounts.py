"""LC-2 — 계정코드 체계(chart of accounts).

Spec: docs/specs/L4_market_data_positions_ledger_v1.0.md#§3.3, §4.4, §9 LC-2.

`AccountCode`는 별도 클래스가 아니라 `str` 형식 규약이다(contracts/v1.py):
"USER:{uuid}:{UserSub}" | "PLATFORM:{NAME}". 이 모듈이 그 형식의 파싱·생성·
유형 판정·음수허용 판정을 전담한다. 부호 규약(§4.4): 자산·비용은 차변 증가,
부채·수익은 대변 증가. `USER:*:RECEIVABLE`만 음수 잔액을 허용하는 유일한
계정(대손 이연을 나타내는 자산)이다.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal
from uuid import UUID

from src.foundation.entities.domain.defaults import (
    default_entity_id,
    default_fund_id,
    default_portfolio_id,
)
from src.foundation.ledger.contracts.v1 import AccountType, UserSub

# FA-0c: the "house" identity (`e7f8a9b0c1d2_wallet_ledger.py`,
# `4a1d0c0de005_ledger_core.py:PLATFORM_HOUSE_USER_ID`) anchors the deterministic
# default entity/fund/portfolio for PLATFORM:* accounts too, since they have no
# user_id of their own to derive from. Must match that constant exactly.
_PLATFORM_SCOPE_ANCHOR = UUID("00000000-0000-0000-0000-000000000001")

PLATFORM_CASH_CLEARING = "PLATFORM:CASH_CLEARING"
PLATFORM_COMMISSION_REVENUE = "PLATFORM:COMMISSION_REVENUE"
PLATFORM_REFUND_RESERVE = "PLATFORM:REFUND_RESERVE"
PLATFORM_PAYOUT_CLEARING = "PLATFORM:PAYOUT_CLEARING"

_PLATFORM_ACCOUNT_TYPES: dict[str, AccountType] = {
    "CASH_CLEARING": AccountType.ASSET,
    "COMMISSION_REVENUE": AccountType.REVENUE,
    "REFUND_RESERVE": AccountType.EXPENSE,
    "PAYOUT_CLEARING": AccountType.CLEARING,
}


class InvalidAccountCodeError(ValueError):
    """`account_code` 문자열이 "USER:{uuid}:{UserSub}" | "PLATFORM:{NAME}" 형식이 아닐 때."""


@dataclass(frozen=True)
class ParsedAccountCode:
    kind: Literal["USER", "PLATFORM"]
    user_id: UUID | None
    sub: UserSub | None
    name: str | None


def user_account(user_id: UUID, sub: UserSub) -> str:
    """"USER:{uuid}:{UserSub}" 계정코드를 생성한다."""
    return f"USER:{user_id}:{sub.value}"


def parse_account_code(account_code: str) -> ParsedAccountCode:
    """`account_code`를 파싱한다. 형식이 어긋나면 `InvalidAccountCodeError`."""
    parts = account_code.split(":")
    if parts and parts[0] == "USER":
        if len(parts) != 3:
            raise InvalidAccountCodeError(
                f"USER 계정코드는 'USER:{{uuid}}:{{UserSub}}' 형식이어야 함: {account_code!r}"
            )
        _, raw_user_id, raw_sub = parts
        try:
            user_id = UUID(raw_user_id)
        except ValueError as exc:
            raise InvalidAccountCodeError(
                f"USER 계정코드의 user_id가 UUID가 아님: {account_code!r}"
            ) from exc
        try:
            sub = UserSub(raw_sub)
        except ValueError as exc:
            raise InvalidAccountCodeError(
                f"USER 계정코드의 서브계정이 UserSub가 아님: {account_code!r}"
            ) from exc
        return ParsedAccountCode(kind="USER", user_id=user_id, sub=sub, name=None)

    if parts and parts[0] == "PLATFORM":
        if len(parts) != 2 or not parts[1]:
            raise InvalidAccountCodeError(
                f"PLATFORM 계정코드는 'PLATFORM:{{NAME}}' 형식이어야 함: {account_code!r}"
            )
        return ParsedAccountCode(kind="PLATFORM", user_id=None, sub=None, name=parts[1])

    raise InvalidAccountCodeError(
        f"계정코드는 'USER:' 또는 'PLATFORM:'으로 시작해야 함: {account_code!r}"
    )


def account_type(account_code: str) -> AccountType:
    """§4.4 계정 성격 판정. 알 수 없는 PLATFORM 이름은 거부한다."""
    parsed = parse_account_code(account_code)
    if parsed.kind == "USER":
        assert parsed.sub is not None
        return AccountType.ASSET if parsed.sub is UserSub.RECEIVABLE else AccountType.LIABILITY

    assert parsed.name is not None
    resolved = _PLATFORM_ACCOUNT_TYPES.get(parsed.name)
    if resolved is None:
        raise InvalidAccountCodeError(f"알 수 없는 PLATFORM 계정: {account_code!r}")
    return resolved


def allows_negative(account_code: str) -> bool:
    """`USER:*:RECEIVABLE`만 음수 잔액을 허용한다(§4.4 유일 예외)."""
    parsed = parse_account_code(account_code)
    return parsed.kind == "USER" and parsed.sub is UserSub.RECEIVABLE


@dataclass(frozen=True)
class AccountScope:
    """FA-0c structural hierarchy scope for a `ledger_account` row."""

    entity_id: UUID
    fund_id: UUID
    portfolio_id: UUID


def default_scope(account_code: str) -> AccountScope:
    """Reverse-derive the FA-0c (entity_id, fund_id, portfolio_id) scope for a
    pre-FA-0c account_code, reusing `entities/domain/defaults.py`'s deterministic
    UUIDv5 rules. USER codes anchor on their own user_id; PLATFORM codes anchor on
    `_PLATFORM_SCOPE_ANCHOR` (the house identity) since they belong to no single
    user. Raises `InvalidAccountCodeError` (via `parse_account_code`) instead of
    guessing when `account_code` does not match the known grammar — migrations must
    fail rather than silently default an unrecognized row."""
    parsed = parse_account_code(account_code)
    anchor = parsed.user_id if parsed.kind == "USER" else _PLATFORM_SCOPE_ANCHOR
    assert anchor is not None
    return AccountScope(
        entity_id=default_entity_id(anchor),
        fund_id=default_fund_id(anchor),
        portfolio_id=default_portfolio_id(anchor),
    )


def portfolio_account(portfolio_id: UUID, acct_type: AccountType) -> str:
    """Display-only `account_code` for a FA-0c portfolio-scoped ledger account.

    Unlike `user_account`/PLATFORM_* codes, this string is never parsed back —
    the real identity lives in the `(entity_id, fund_id, portfolio_id,
    account_type)` columns (`ledger_account`). The string embeds `portfolio_id`
    only so it stays compatible with the table's pre-existing `UNIQUE(account_code)`
    constraint; application code must read the columns, not this string, to learn
    an account's hierarchy scope."""
    return f"PORTFOLIO:{portfolio_id}:{acct_type.value}"
