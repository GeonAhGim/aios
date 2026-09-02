"""LC-2 — chart_of_accounts 단위테스트.

Spec: docs/specs/L4_market_data_positions_ledger_v1.0.md#§3.3, §4.4, §9 LC-2.
"""
from uuid import UUID, uuid4

import pytest

from src.foundation.ledger.contracts.v1 import AccountType, UserSub
from src.foundation.ledger.domain import chart_of_accounts as coa

_USER_ID = UUID("12345678-1234-5678-1234-567812345678")


def test_user_account_formats_uuid_and_sub() -> None:
    code = coa.user_account(_USER_ID, UserSub.AVAILABLE)

    assert code == "USER:12345678-1234-5678-1234-567812345678:AVAILABLE"


def test_parse_account_code_round_trips_user_account() -> None:
    code = coa.user_account(_USER_ID, UserSub.HELD)

    parsed = coa.parse_account_code(code)

    assert parsed.kind == "USER"
    assert parsed.user_id == _USER_ID
    assert parsed.sub is UserSub.HELD
    assert parsed.name is None


def test_parse_account_code_parses_platform_account() -> None:
    parsed = coa.parse_account_code(coa.PLATFORM_CASH_CLEARING)

    assert parsed.kind == "PLATFORM"
    assert parsed.user_id is None
    assert parsed.sub is None
    assert parsed.name == "CASH_CLEARING"


@pytest.mark.parametrize(
    "sub, expected",
    [
        (UserSub.AVAILABLE, AccountType.LIABILITY),
        (UserSub.HELD, AccountType.LIABILITY),
        (UserSub.PENDING_PAYOUT, AccountType.LIABILITY),
        (UserSub.RECEIVABLE, AccountType.ASSET),
    ],
)
def test_account_type_for_user_sub(sub: UserSub, expected: AccountType) -> None:
    code = coa.user_account(_USER_ID, sub)

    assert coa.account_type(code) is expected


@pytest.mark.parametrize(
    "code, expected",
    [
        (coa.PLATFORM_CASH_CLEARING, AccountType.ASSET),
        (coa.PLATFORM_COMMISSION_REVENUE, AccountType.REVENUE),
        (coa.PLATFORM_REFUND_RESERVE, AccountType.EXPENSE),
        (coa.PLATFORM_PAYOUT_CLEARING, AccountType.CLEARING),
    ],
)
def test_account_type_for_platform_constants(code: str, expected: AccountType) -> None:
    assert coa.account_type(code) is expected


@pytest.mark.parametrize(
    "sub, expected",
    [
        (UserSub.AVAILABLE, False),
        (UserSub.HELD, False),
        (UserSub.PENDING_PAYOUT, False),
        (UserSub.RECEIVABLE, True),
    ],
)
def test_allows_negative_only_true_for_receivable(sub: UserSub, expected: bool) -> None:
    code = coa.user_account(_USER_ID, sub)

    assert coa.allows_negative(code) is expected


def test_allows_negative_false_for_platform_accounts() -> None:
    assert coa.allows_negative(coa.PLATFORM_COMMISSION_REVENUE) is False


def test_parse_rejects_unknown_prefix() -> None:
    with pytest.raises(coa.InvalidAccountCodeError):
        coa.parse_account_code("TENANT:acme:AVAILABLE")


def test_parse_rejects_user_account_with_wrong_segment_count() -> None:
    with pytest.raises(coa.InvalidAccountCodeError):
        coa.parse_account_code(f"USER:{uuid4()}")


def test_parse_rejects_user_account_with_non_uuid() -> None:
    with pytest.raises(coa.InvalidAccountCodeError):
        coa.parse_account_code("USER:not-a-uuid:AVAILABLE")


def test_parse_rejects_user_account_with_unknown_sub() -> None:
    with pytest.raises(coa.InvalidAccountCodeError):
        coa.parse_account_code(f"USER:{uuid4()}:UNKNOWN_SUB")


def test_parse_rejects_platform_account_with_empty_name() -> None:
    with pytest.raises(coa.InvalidAccountCodeError):
        coa.parse_account_code("PLATFORM:")


def test_account_type_rejects_unknown_platform_name() -> None:
    with pytest.raises(coa.InvalidAccountCodeError):
        coa.account_type("PLATFORM:NOT_A_REAL_ACCOUNT")


def test_parse_rejects_empty_string() -> None:
    with pytest.raises(coa.InvalidAccountCodeError):
        coa.parse_account_code("")
