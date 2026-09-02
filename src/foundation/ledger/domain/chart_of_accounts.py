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

from src.foundation.ledger.contracts.v1 import AccountType, UserSub

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
