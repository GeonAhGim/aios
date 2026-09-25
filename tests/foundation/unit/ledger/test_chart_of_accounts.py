"""LC-2 — chart_of_accounts 단위테스트.

Spec: docs/specs/L4_market_data_positions_ledger_v1.0.md#§3.3, §4.4, §9 LC-2.

DEPTH 감사(task-2724, docs/audit/DEPTH_FA.md)가 원 task-1942/FA-0c(commit
4c925cf4)의 D3 하한 미달로 지적한 공백 중 리플레이 증거와 성능 단언을
`test_default_scope_is_deterministic_across_repeated_replays`와
`test_default_scope_derivation_throughput_stays_within_budget`로 여기서
메운다(task-3031). 적대적 증거는 I/O가 필요해
tests/integration/foundation/ledger/test_fa0c_account_scope.py에 있고,
동시성(D3)은 그 파일에 이미 있다(task-2474/qa-2474,
test_concurrent_inserts_for_same_scope_and_type_serialize_to_one_winner).
"""

import time
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


def test_default_scope_is_deterministic_for_user_account() -> None:
    code = coa.user_account(_USER_ID, UserSub.AVAILABLE)

    scope_a = coa.default_scope(code)
    scope_b = coa.default_scope(code)

    assert scope_a == scope_b


def test_default_scope_differs_across_users() -> None:
    code_a = coa.user_account(_USER_ID, UserSub.AVAILABLE)
    code_b = coa.user_account(uuid4(), UserSub.AVAILABLE)

    assert coa.default_scope(code_a) != coa.default_scope(code_b)


def test_default_scope_same_for_every_sub_account_of_one_user() -> None:
    """Different sub-accounts of the same user share one (entity, fund, portfolio)
    triple — they are disambiguated by `account_type`, not by scope."""
    available = coa.default_scope(coa.user_account(_USER_ID, UserSub.AVAILABLE))
    held = coa.default_scope(coa.user_account(_USER_ID, UserSub.HELD))

    assert available == held


def test_default_scope_anchors_platform_accounts_on_house_identity() -> None:
    house_user_code = coa.user_account(
        UUID("00000000-0000-0000-0000-000000000001"), UserSub.AVAILABLE
    )

    assert coa.default_scope(coa.PLATFORM_CASH_CLEARING) == coa.default_scope(house_user_code)


def test_default_scope_rejects_unparseable_account_code() -> None:
    with pytest.raises(coa.InvalidAccountCodeError):
        coa.default_scope("TENANT:acme:AVAILABLE")


def test_portfolio_account_embeds_portfolio_id_and_type() -> None:
    portfolio_id = uuid4()

    code = coa.portfolio_account(portfolio_id, AccountType.ASSET)

    assert code == f"PORTFOLIO:{portfolio_id}:ASSET"


def test_portfolio_account_differs_across_portfolios_for_same_type() -> None:
    code_a = coa.portfolio_account(uuid4(), AccountType.ASSET)
    code_b = coa.portfolio_account(uuid4(), AccountType.ASSET)

    assert code_a != code_b


def test_default_scope_is_deterministic_across_repeated_replays() -> None:
    """리플레이 증거 -- 마이그레이션 18965d657219의 백필은 각 시드 행의
    account_code를 한 번만 지나가지만, 같은 계정코드가 감사 재현·마이그레이션
    리허설 등으로 반복해서 default_scope()를 다시 거칠 수 있다. 같은
    account_code를 200회 반복 호출해도 완전히 동일한
    (entity_id, fund_id, portfolio_id) 삼중값이 나오는지(비결정성 없음)
    검증한다 -- 한 번이라도 흔들리면 마이그레이션과 런타임이 서로 다른
    스코프를 계산해 UNIQUE 위반 또는 조용한 스코프 분기로 이어진다."""
    code = coa.user_account(_USER_ID, UserSub.AVAILABLE)

    replays = [coa.default_scope(code) for _ in range(200)]

    assert len(set(replays)) == 1


@pytest.mark.perf
def test_default_scope_derivation_throughput_stays_within_budget() -> None:
    """수치 성능 단언 -- DEPTH 재감사(task-2724)가 지적한 공백을 메운다.
    `default_scope()`는 I/O 없는 순수 함수지만 계정 조회 경로마다 호출될 수
    있으므로(마이그레이션 백필, 향후 FA-4/FA-8 포트폴리오 배선), 서로 다른
    20,000개 계정코드에 대한 파싱+UUIDv5 3회 도출 처리량이 예산 아래인지
    단언한다."""
    n = 20_000
    budget_sec = 2.0
    min_ops_per_sec = 10_000.0
    codes = [coa.user_account(uuid4(), UserSub.AVAILABLE) for _ in range(n)]

    start = time.perf_counter()
    for code in codes:
        scope = coa.default_scope(code)
        assert scope.entity_id is not None
    elapsed = time.perf_counter() - start
    ops_per_sec = n / elapsed

    print(
        f"[task-1942/3031 default_scope] {n} calls {elapsed:.3f}s "
        f"({ops_per_sec:.0f} ops/s, budget<{budget_sec}s, min>{min_ops_per_sec:.0f} ops/s)"
    )
    assert elapsed < budget_sec, (
        f"{n}회 default_scope() 호출이 예산({budget_sec}s)을 넘었습니다({elapsed:.3f}s)."
    )
    assert ops_per_sec > min_ops_per_sec, (
        f"default_scope() 처리량이 최소값({min_ops_per_sec:.0f} ops/s)에 "
        f"못 미칩니다({ops_per_sec:.0f})."
    )
