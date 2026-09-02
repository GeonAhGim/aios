"""LC-11 — 기존 wallet_transactions → 원장 소급 적재.

Spec: docs/specs/L4_market_data_positions_ledger_v1.0.md#§9 LC-11, R1~R3.

새 포스팅 경로를 만들지 않는다 — `wallet_transactions` 행을 그룹으로 묶어
`LedgerEvent`(LC-1)로 재구성한 뒤 `post_entry`(LC-9, task-330)에 넘기는
것뿐이다. 실제 분개 내용(어느 계정이 차변/대변인지)은 여기서 결정하지
않는다 — `post_entry`가 내부에서 호출하는 `posting_rules.lines_for`(LC-4)
가 유일한 결정권자다.

행 → 사건 매핑(§4.4 계정 성격은 posting_rules가 알고 있으므로 여기서는
"어떤 사건이었는가"만 복원한다):

- `TOPUP`(단독, `related_purchase_id IS NULL`) → `TOPUP_CONFIRMED`.
- `PURCHASE_DEBIT`+`SALE_CREDIT`(+`COMMISSION_CREDIT`)가 같은
  `related_purchase_id`로 묶이면 구매 1건 → `HOLD_PLACED` → `HOLD_CAPTURED`
  → `PAYOUT_RELEASE`. 구 지갑 모델은 "판매대금 즉시 정산"(홀드 창 없음,
  §10 R2)이었는데 `HOLD_CAPTURED`는 항상 `PENDING_PAYOUT`으로 대변
  처리하므로(현행 정책, 변경 대상 아님), 곧장 `PAYOUT_RELEASE`로
  `AVAILABLE`에 옮겨 "기존 판매자 잔액은 AVAILABLE 유지"를 재현한다.
  커미션은 `commission_amount / price`를 역산한 rate로 `split_commission`
  (LC-2, HALF_EVEN)에 되돌려 넣는다 — 분모·분자가 원래 그 나눗셈에서
  나왔으므로 재양자화 후 원래 값과 정확히 같다(R3: 실제 값 그대로).
- `REFUND` + 클로백 3종(`REFUND_SELLER_CLAWBACK`/`_COMMISSION_CLAWBACK`/
  `_SHORTFALL_COVER`) 세트 → 총잔액 보존형 환불이므로 `REFUND`
  이벤트(R2: 부족분 없음, R3: `REFUND_SHORTFALL_COVER` 존재)로 복원.
- `REFUND` 단독(클로백 행 없음) → 레드팀 #41 이전의 자금창출형 환불(§1.1
  C2, R1). 판매자·플랫폼 회수가 애초에 없었던 이력이므로 그대로 재현하면
  Σ≠0이 된다 — 대신 `MANUAL_ADJUSTMENT`로 차변을 `PLATFORM:REFUND_RESERVE`
  (LC-6 시드, EXPENSE — 손실 인식)에 걸어 창출분을 명시적으로 흡수한다.

계정 생성: `post_entry`/`get_for_update`는 존재하지 않는 계정을 조용히
만들지 않는다(fail-closed, §5) — `USER:*` 계정은 사건 처리 전에
`ON CONFLICT DO NOTHING`으로 미리 만든다. `PLATFORM:*`은 LC-6 시드.

원자성: `conn`만 받고 스스로 커밋하지 않는다(post_entry와 동일 계약). 잔액
불일치가 있으면 `BackfillMismatchError`를 던지고, 호출자가 잡지 않고
전파해야 트랜잭션 전체가 롤백된다(부분 적재 금지).
"""
from __future__ import annotations

from collections import defaultdict
from decimal import Decimal
from uuid import UUID, uuid4

import asyncpg
from pydantic import BaseModel

from src.data.models.base import Currency
from src.foundation.ledger.application.post_entry import AuditAppender, Clock, post_entry
from src.foundation.ledger.contracts.v1 import LedgerEvent, LedgerEventType, UserSub
from src.foundation.ledger.domain.chart_of_accounts import (
    PLATFORM_REFUND_RESERVE,
    account_type,
    allows_negative,
)
from src.foundation.ledger.domain.chart_of_accounts import user_account as ua
from src.foundation.ledger.ports.balance_repository import BalanceRepository
from src.foundation.ledger.ports.journal_repository import LedgerJournalRepository

_PURCHASE_TX_TYPES = frozenset({"PURCHASE_DEBIT", "SALE_CREDIT", "COMMISSION_CREDIT"})
_CLAWBACK = ("REFUND_SELLER_CLAWBACK", "REFUND_COMMISSION_CLAWBACK", "REFUND_SHORTFALL_COVER")
_CLAWBACK_TX_TYPES = frozenset(_CLAWBACK)
_KNOWN_TX_TYPES = _PURCHASE_TX_TYPES | _CLAWBACK_TX_TYPES | {"TOPUP", "REFUND"}


class LegacyWalletTx(BaseModel):
    """`wallet_transactions` 한 행. `amount`는 원본 컬럼과 같은 부호
    (`wallet_service.debit/credit`이 각각 음수/양수로 저장, §9 LC-11)."""

    id: int
    user_id: UUID
    tx_type: str
    amount: Decimal
    related_purchase_id: int | None = None


class BackfillReport(BaseModel):
    entries_posted: int
    accounts_verified: int


class UnrecognizedTxGroupError(Exception):
    """`related_purchase_id`로 묶인 조합이 알려진 패턴(구매/환불)에 맞지
    않는다 — 부분 해석으로 잘못된 분개를 만드느니 fail-closed로 전체를
    거부한다."""


class BackfillMismatchError(Exception):
    """적재 후 `ledger_balance`가 기대 잔액과 다르다 — 호출자는 이를 잡지
    말고 전파해 트랜잭션 전체를 롤백해야 한다(부분 적재 금지)."""

    def __init__(self, mismatches: list[tuple[str, Decimal, Decimal]]) -> None:
        detail = ", ".join(
            f"{code}: ledger={actual} expected={expected}" for code, actual, expected in mismatches
        )
        super().__init__(f"백필 후 잔액 불일치({len(mismatches)}건): {detail}")
        self.mismatches = mismatches


def _event(
    event_type: LedgerEventType,
    ref: str,
    amount: Decimal,
    currency: Currency,
    parties: dict[str, UUID],
    extra: dict[str, Decimal | str],
) -> LedgerEvent:
    return LedgerEvent(
        event_type=event_type,
        event_ref=ref,
        tenant_id=None,
        actor_subject_id=None,
        trace_id=uuid4(),
        amount=amount,
        currency=currency,
        parties=parties,
        extra=extra,
    )


def _rate_for(amount: Decimal, total: Decimal) -> Decimal:
    """`split_commission(total, rate)`이 정확히 `amount`를 복원하도록 역산한
    비율. `amount`가 원래 `total`에서 반올림돼 나온 값이므로 왕복이 정확하다."""
    return amount / total if amount > 0 else Decimal("0")


def _purchase_events(
    ref: str, rows_by_type: dict[str, LegacyWalletTx], currency: Currency
) -> list[LedgerEvent]:
    debit_row = rows_by_type.get("PURCHASE_DEBIT")
    credit_row = rows_by_type.get("SALE_CREDIT")
    if debit_row is None or credit_row is None:
        raise UnrecognizedTxGroupError(f"{ref}: 불완전한 구매 세트 {sorted(rows_by_type)}")
    buyer, seller = debit_row.user_id, credit_row.user_id
    price = -debit_row.amount
    payout = credit_row.amount
    commission_row = rows_by_type.get("COMMISSION_CREDIT")
    commission = commission_row.amount if commission_row is not None else Decimal("0")
    rate = _rate_for(commission, price)
    return [
        _event(LedgerEventType.HOLD_PLACED, ref, price, currency, {"buyer": buyer}, {}),
        _event(
            LedgerEventType.HOLD_CAPTURED, ref, price, currency,
            {"buyer": buyer, "seller": seller}, {"commission_rate": rate},
        ),
        _event(LedgerEventType.PAYOUT_RELEASE, ref, payout, currency, {"seller": seller}, {}),
    ]


def _refund_events(
    ref: str, rows_by_type: dict[str, LegacyWalletTx], seller_hint: UUID | None, currency: Currency
) -> list[LedgerEvent]:
    refund_row = rows_by_type["REFUND"]
    buyer = refund_row.user_id
    price = refund_row.amount

    seller_row = rows_by_type.get("REFUND_SELLER_CLAWBACK")
    commission_row = rows_by_type.get("REFUND_COMMISSION_CLAWBACK")
    shortfall_row = rows_by_type.get("REFUND_SHORTFALL_COVER")
    if seller_row is None and commission_row is None and shortfall_row is None:
        # 클로백 행이 전혀 없는 이력 = 레드팀 #41 이전 자금창출형 환불(R1).
        return [
            _event(
                LedgerEventType.MANUAL_ADJUSTMENT, ref, price, currency, {},
                {
                    "debit_account": PLATFORM_REFUND_RESERVE,
                    "credit_account": ua(buyer, UserSub.AVAILABLE),
                },
            )
        ]

    seller = seller_row.user_id if seller_row is not None else seller_hint
    if seller is None:
        raise UnrecognizedTxGroupError(
            f"{ref}: 클로백 세트에 seller를 특정할 REFUND_SELLER_CLAWBACK 행도, "
            "같은 그룹의 구매 세트도 없음"
        )
    seller_share = -seller_row.amount if seller_row is not None else Decimal("0")
    commission_share = -commission_row.amount if commission_row is not None else Decimal("0")
    shortfall = -shortfall_row.amount if shortfall_row is not None else Decimal("0")
    rate = _rate_for(commission_share, price)

    extra: dict[str, Decimal | str] = {"commission_rate": rate}
    if shortfall > 0:
        extra["refund_case"] = "R3"
        extra["seller_available_amount"] = seller_share
    else:
        extra["refund_case"] = "R2"
    parties = {"buyer": buyer, "seller": seller}
    return [_event(LedgerEventType.REFUND, ref, price, currency, parties, extra)]


def _build_events(rows: list[LegacyWalletTx], currency: Currency) -> list[LedgerEvent]:
    """`rows`를 원본 `id`(발생 순서, BIGSERIAL) 기준으로 사건화한다. 순서가
    어긋나면 중간 잔액이 음수로 떨어져 `balance_rules`가 거부한다."""
    ordered: list[tuple[tuple[int, int], LedgerEvent]] = []

    grouped: dict[int, list[LegacyWalletTx]] = defaultdict(list)
    for row in rows:
        if row.related_purchase_id is None:
            if row.tx_type != "TOPUP":
                raise UnrecognizedTxGroupError(f"row {row.id}: 그룹 없는 {row.tx_type!r}")
            ordered.append(((row.id, 0), _event(
                LedgerEventType.TOPUP_CONFIRMED, f"backfill:topup:{row.id}", row.amount,
                currency, {"user": row.user_id}, {},
            )))
            continue
        unknown = row.tx_type not in _KNOWN_TX_TYPES
        if unknown:
            raise UnrecognizedTxGroupError(f"row {row.id}: 알 수 없는 tx_type {row.tx_type!r}")
        grouped[row.related_purchase_id].append(row)

    for purchase_id, group in grouped.items():
        by_type = {row.tx_type: row for row in group}
        if len(by_type) != len(group):
            raise UnrecognizedTxGroupError(f"purchase {purchase_id}: 같은 tx_type 중복")

        purchase_rows = {t: r for t, r in by_type.items() if t in _PURCHASE_TX_TYPES}
        refund_rows = {t: r for t, r in by_type.items() if t in _CLAWBACK_TX_TYPES or t == "REFUND"}

        seller_hint: UUID | None = None
        if purchase_rows:
            ref = f"backfill:purchase:{purchase_id}"
            purchase_events = _purchase_events(ref, purchase_rows, currency)  # 불완전 세트면 raise
            primary_id = purchase_rows["PURCHASE_DEBIT"].id
            for sub_index, event in enumerate(purchase_events):
                ordered.append(((primary_id, sub_index), event))
            seller_hint = purchase_rows["SALE_CREDIT"].user_id

        if "REFUND" in refund_rows:
            ref = f"backfill:refund:{purchase_id}"
            primary_id = refund_rows["REFUND"].id
            refund_events = _refund_events(ref, refund_rows, seller_hint, currency)
            for sub_index, event in enumerate(refund_events):
                ordered.append(((primary_id, sub_index), event))
        elif refund_rows:
            raise UnrecognizedTxGroupError(
                f"purchase {purchase_id}: REFUND 없이 클로백 행만 존재 {sorted(refund_rows)}"
            )

    ordered.sort(key=lambda item: item[0])
    return [event for _, event in ordered]


async def _ensure_user_account(
    conn: asyncpg.Connection, account_code: str, currency: Currency
) -> None:
    negative_ok = allows_negative(account_code)
    await conn.execute(
        "INSERT INTO ledger_account (account_code, account_type, currency, allow_negative) "
        "VALUES ($1, $2, $3, $4) ON CONFLICT (account_code) DO NOTHING",
        account_code, account_type(account_code).value, currency.value, negative_ok,
    )
    await conn.execute(
        "INSERT INTO ledger_balance (account_id, allow_negative) "
        "SELECT account_id, $2 FROM ledger_account WHERE account_code = $1 "
        "ON CONFLICT (account_id) DO NOTHING",
        account_code, negative_ok,
    )


async def backfill_ledger(
    conn: asyncpg.Connection,
    rows: list[LegacyWalletTx],
    expected_balances: dict[UUID, Decimal],
    *,
    journal: LedgerJournalRepository,
    balances: BalanceRepository,
    audit: AuditAppender,
    clock: Clock,
    currency: Currency = Currency.KRW,
) -> BackfillReport:
    """`rows`(순서 무관, 내부에서 `id`로 재정렬) 전부를 `post_entry` 단일
    경로로 적재한 뒤 `expected_balances`(`user_id → 기대 AVAILABLE 잔액`,
    예: `user_wallets.balance`)와 `ledger_balance`를 대조한다. `conn`은
    호출자가 이미 연 트랜잭션이어야 하며 커밋/롤백을 결정하지 않는다."""
    events = _build_events(rows, currency)

    for user_id in {uid for event in events for uid in event.parties.values()}:
        for sub in UserSub:
            await _ensure_user_account(conn, ua(user_id, sub), currency)

    for event in events:
        await post_entry(conn, event, journal=journal, balances=balances, audit=audit, clock=clock)

    mismatches: list[tuple[str, Decimal, Decimal]] = []
    if expected_balances:
        codes = sorted({ua(uid, UserSub.AVAILABLE) for uid in expected_balances})
        current = await balances.get_for_update(conn, codes)
        for user_id, expected in expected_balances.items():
            code = ua(user_id, UserSub.AVAILABLE)
            actual = current[code].balance
            if actual != expected:
                mismatches.append((code, actual, expected))
    if mismatches:
        raise BackfillMismatchError(mismatches)

    return BackfillReport(entries_posted=len(events), accounts_verified=len(expected_balances))
