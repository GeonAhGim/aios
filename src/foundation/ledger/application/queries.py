"""LC-16 — 조회 전용 애플리케이션 계층(원장 잔액).

Spec: docs/specs/L4_market_data_positions_ledger_v1.0.md#§2.4, §4.4, §9 LC-16.

이번 리프는 `get_balance`만 구현한다 — §9 DoD가 요구하는 것은 `GET /wallet`
응답(`src/api/routers/wallet.py`)뿐이고, `list_entries`/`trial_balance`(§2.4
표에 예약된 이름)는 이 DoD에 필요 없어 후속 리프로 남긴다.

`user_wallets.balance`(레거시 투영, LC-12 §5.4)와 `USER:{u}:AVAILABLE.balance`
(원장 진실)는 정상 경로에서는 항상 같아야 한다 — `legacy_wallet_bridge`·
`purchase_flow`의 `_reconcile_*`가 매 쓰기 경로마다 두 값을 맞춘다. 이
조회 경로는 그 reconcile을 스스로 수행하지 않는다: GET 요청이 부수효과로
원장에 `MANUAL_ADJUSTMENT` 분개를 남기면 71번 §6(조회는 read-only) 위반이고,
있어서는 안 될 드리프트를 조용히 지워버리면 운영자가 원인을 조사할 기회를
잃는다. 대신 드리프트를 발견하면 `WalletLedgerDriftError`로 명시적으로
실패한다(§9 LC-16 DoD negative test) — `src/api/contracts/exception_mapping.py`
가 이를 `INTEGRITY_WALLET_BALANCE_DRIFT`(409)로 매핑해 500(INTERNAL_ERROR)과
구분되는 신호를 호출자에게 준다.

(task-951, LC-16 결함 수정) `get_balance`는 레거시 잔액과 세 원장 계정을
**단일 SQL 왕복**(`LEFT JOIN`, `get_balance` 본문 참조)으로 읽는다 — 네 번의
개별 SELECT로 나누면 각 문장이 자기만의 MVCC 스냅샷을 얻어, 조회 도중
다른 트랜잭션이 커밋하면 legacy와 ledger가 "실제로는 드리프트가 없는데도"
서로 다른 시점의 값을 비교해 위양성 409를 던질 수 있었다(task-944 리뷰
REJECT). 한 문장으로 합치면 Postgres가 문장 시작 시점의 스냅샷 하나로
모든 서브 결과를 평가하므로 이 레이스가 구조적으로 사라진다 — 드리프트
감지 자체(fail-closed 409)는 그대로 유지한다.

`HELD`/`PENDING_PAYOUT` 계정은 활동이 없으면 아직 `ledger_account`에 행이
없을 수 있다(지연 생성 — `purchase_flow.ensure_account` 등, LC-8b
`UnknownAccountError` fail-closed 계약) — 이 모듈은 그 경우를 조용히 0으로
취급한다(활동이 없던 신규 사용자에게 0이 아닌 다른 값을 기대할 근거가
없으므로 이건 드리프트가 아니다).
"""
from __future__ import annotations

from decimal import Decimal
from uuid import UUID

import asyncpg
from pydantic import BaseModel

from src.foundation.ledger.contracts.v1 import UserSub
from src.foundation.ledger.domain.chart_of_accounts import user_account as ua
from src.foundation.ledger.ports.balance_repository import BalanceRepository


class WalletLedgerDriftError(Exception):
    """레거시 `user_wallets.balance`와 원장 `USER:*:AVAILABLE` 잔액이 어긋났다.
    조회 경로는 §4.4 `MANUAL_ADJUSTMENT`로 스스로 봉합하지 않는다(그건 쓰기
    경로 전용 — `legacy_wallet_bridge`/`purchase_flow`의 `_reconcile_*` 책임).
    운영자가 원인을 확인할 때까지 명시적으로 실패한다(fail-closed, 모듈
    docstring 참조)."""

    def __init__(
        self, user_id: UUID, *, legacy_balance: Decimal, ledger_available: Decimal
    ) -> None:
        super().__init__(
            f"wallet drift user_id={user_id}: user_wallets.balance={legacy_balance} != "
            f"ledger USER:AVAILABLE={ledger_available}"
        )
        self.user_id = user_id
        self.legacy_balance = legacy_balance
        self.ledger_available = ledger_available


class WalletBalanceView(BaseModel):
    """`GET /wallet/balance` 응답 — 기존 `balance` 필드는 그대로 두고
    `available`/`held`/`pending_payout`을 추가한다(§9 LC-16 DoD, MINOR
    변경 — 프론트 무변경 통과)."""

    user_id: UUID
    balance: Decimal
    available: Decimal
    held: Decimal
    pending_payout: Decimal


_BALANCE_SNAPSHOT_SQL = (
    "SELECT codes.account_code, lb.balance AS ledger_balance, "
    "uw.balance AS legacy_balance "
    "FROM unnest($2::text[]) AS codes(account_code) "
    "LEFT JOIN ledger_account la ON la.account_code = codes.account_code "
    "LEFT JOIN ledger_balance lb ON lb.account_id = la.account_id "
    "LEFT JOIN user_wallets uw ON uw.user_id = $1"
)


async def get_balance(
    pool: asyncpg.Pool, user_id: UUID, *, balances: BalanceRepository
) -> WalletBalanceView:
    """§9 LC-16 DoD: `balance`는 `user_wallets`(레거시 투영) 그대로,
    `available`/`held`/`pending_payout`은 원장(LC-8b/13/15가 만든 계정)에서
    읽는다. 네 값을 한 SQL 왕복(`_BALANCE_SNAPSHOT_SQL`, `LEFT JOIN` — 계정이
    아직 지연 생성되지 않았으면 `ledger_balance`가 NULL이고 이를 0으로
    취급한다)으로 묶어 Postgres 문장 시작 시점의 단일 스냅샷에서 읽는다.
    `BalanceRepository.get_for_update`(§5 C, 쓰기 경로의 행 잠금)는 read-only
    조회에 불필요한 잠금이라 여기서는 쓰지 않는다 — `balances` 인자는
    라우터(`src/api/routers/wallet.py`)와의 시그니처 호환을 위해 유지하되
    본문에서는 참조하지 않는다. 잔액 부족(402) 같은 쓰기 검증은 여기서
    하지 않는다 — 이 함수는 절대 쓰지 않는다."""
    del balances
    available_code = ua(user_id, UserSub.AVAILABLE)
    held_code = ua(user_id, UserSub.HELD)
    pending_payout_code = ua(user_id, UserSub.PENDING_PAYOUT)
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            _BALANCE_SNAPSHOT_SQL,
            user_id,
            [available_code, held_code, pending_payout_code],
        )

    by_code = {row["account_code"]: row["ledger_balance"] for row in rows}
    legacy_raw = rows[0]["legacy_balance"]
    legacy_balance = legacy_raw if legacy_raw is not None else Decimal("0")
    available = by_code[available_code] if by_code[available_code] is not None else Decimal("0")
    held = by_code[held_code] if by_code[held_code] is not None else Decimal("0")
    pending_payout = (
        by_code[pending_payout_code]
        if by_code[pending_payout_code] is not None
        else Decimal("0")
    )

    if legacy_balance != available:
        raise WalletLedgerDriftError(
            user_id, legacy_balance=legacy_balance, ledger_available=available
        )

    return WalletBalanceView(
        user_id=user_id,
        balance=legacy_balance,
        available=available,
        held=held,
        pending_payout=pending_payout,
    )
