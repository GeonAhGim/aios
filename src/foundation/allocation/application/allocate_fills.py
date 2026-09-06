"""FA-8 — allocation/application/allocate_fills.py: 체결 → sub_account 배분
+ 원장 연결.

Spec: docs/specs/L4_ibor_fund_accounting_and_resilience_v1.0.md#FA-8
(§9 표·§2.1 allocation 행).

이 유스케이스는 FA-7의 순수 도메인(`domain/policy.allocate`,
`domain/average_price.blended_average_price`/`apply_average_price`)에
"체결 조회 → 배분 계산 → sub_account별 원장 분개 → 배분 기록 저장"이라는
I/O 오케스트레이션만 얹는다 — 배분 정책·가중평균 계산 자체를
재구현하지 않는다(PM decision, task-1796).

원장 연결은 LC-4 `posting_rules`/LC-9 `post_entry`를 그대로 재사용한다.
`posting_rules`가 아는 9종 사건에 새 사건을 추가하지 않고, 기존
`MANUAL_ADJUSTMENT`(임의 차변/대변 계정 쌍 + 단일 금액)로 sub_account별
배분 1건마다 분개 1건을 만든다 — N-way 배분을 한 분개로 표현하려면
LC-4에 새 사건 타입이 필요한데 그건 "새 분개 규칙 신설 금지"에
어긋나므로, 대신 sub_account 수만큼 2-line 분개를 반복한다. 각 분개는
독립적으로 균형(차변=대변)이라 `balance_rules.check_balanced`
(post_entry 내부)를 그대로 만족한다.

계정 코드는 `SubAccount.owner_ref`(FK `users.user_id`, FA-2 마이그레이션 —
그래서 기존 `USER:*` 지갑 계정과 같은 식별자 공간이다)로 만든
`USER:{owner_ref}:AVAILABLE` — 이 배분 대상의 실소유자 지갑이다. 반대쪽은
기존 `PLATFORM:CASH_CLEARING`(LC-2, topup/purchase_flow가 이미 쓰는 정산
청산 계정): 매수는 지갑에서 청산 계정으로, 매도는 반대 방향으로 흐른다
(§4.4 자산·비용 차변 증가/부채·수익 대변 증가 부호 규약은
`chart_of_accounts.account_type`이 최종 결정하므로 여기서 재구현하지
않는다). `owner_ref`용 `ledger_account`/`ledger_balance` 행이 없으면
`topup.py::_reconcile_ledger_with_projection`과 같은 패턴으로 최초 1회
만든다(계정 프로비저닝은 posting_rules/post_entry의 계약 밖이다 — LC-9는
"미지 계정은 fail-closed로 거부"이므로 이 파일이 맡는다).

멱등: sub_account별 분개의 `event_ref`를 `f"fill_allocation:{order_id}:
{sub_account_id}"`로 고정해 LC-3(`post_entry`가 쓰는 `idempotency_key`)가
재시도를 그대로 흡수한다. PLT-14(I-03 4중 스코프)는 HTTP POST
진입점의 `Idempotency-Key` 헤더 스코프(route+tenant_id+subject_id+
header_key)라 이 내부 오케스트레이션 함수(HTTP 엔드포인트가 아니다)에는 그 스코프 자체가
성립하지 않는다 — 대신 같은 "결정론적 키 + 조건부 삽입" 원칙을 이 함수의
자연키(order_id, sub_account_id)로 적용한다. `fill_allocation` 테이블의
`UNIQUE(order_id, sub_account_id)`도 자체 중복검사 테이블이 아니라 배분
사실 자체의 무결성 제약이다(마이그레이션 docstring 참고).

FA-A3(배분 합계 == 체결, 오차 ≤ 1 최소단위)는 `policy.allocate`/
`average_price.apply_average_price`가 이미 강제하므로 이 파일은
재검증하지 않는다. 분개 금액(`LedgerEvent.amount`)은 `PostingLine.amount`
(계약, `decimal_places=2`)·`ledger_posting_line.amount NUMERIC(20,2)`
(LC-1 §3.3) 제약에 맞춰 raw notional(quantity × average_price, 무손실)을
0.01 단위로 1회 반올림(`policy.round_to_quantum` 재사용)한 값이다 —
`fill_allocation.quantity`/`average_price`는 원장에 싣지 않은 원본
정밀도(NUMERIC(30,10))를 그대로 보존한다.
"""
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal
from uuid import UUID

import asyncpg

from src.data.models.base import Currency
from src.foundation.allocation.domain.average_price import (
    PartialFill,
    SubAccountAllocation,
    apply_average_price,
    blended_average_price,
)
from src.foundation.allocation.domain.policy import (
    AllocationPolicy,
    ManualTarget,
    WeightTarget,
    allocate,
    round_to_quantum,
)
from src.foundation.entities.application.resolve_context import EntityRepository
from src.foundation.ledger.application.post_entry import AuditAppender, Clock, post_entry
from src.foundation.ledger.contracts.v1 import LedgerEvent, LedgerEventType
from src.foundation.ledger.contracts.v1 import UserSub as LedgerUserSub
from src.foundation.ledger.domain.chart_of_accounts import (
    PLATFORM_CASH_CLEARING,
    account_type,
    allows_negative,
)
from src.foundation.ledger.domain.chart_of_accounts import user_account as ua
from src.foundation.ledger.ports.balance_repository import BalanceRepository
from src.foundation.ledger.ports.journal_repository import LedgerJournalRepository

_NOTIONAL_QUANTUM = Decimal("0.01")


class NoFillsError(ValueError):
    """`order_id`에 대한 체결이 하나도 없어 배분할 대상이 없다."""


class AllocationTargetError(ValueError):
    """배분 대상 sub_account/portfolio/fund가 없거나 폐쇄됐다(fail-closed)."""


@dataclass(frozen=True)
class FillAllocationResult:
    """sub_account 하나에 대한 배분 + 원장 분개 결과."""

    sub_account_id: UUID
    quantity: Decimal
    average_price: Decimal
    fund_id: UUID
    portfolio_id: UUID
    ledger_entry_id: UUID
    replayed: bool


@dataclass(frozen=True)
class _ResolvedTarget:
    owner_ref: UUID
    portfolio_id: UUID
    fund_id: UUID
    base_currency: Currency


async def _resolve_target(
    entities: EntityRepository, tenant_id: UUID, sub_account_id: UUID
) -> _ResolvedTarget:
    """`sub_account_id` → (owner_ref, portfolio_id, fund_id, base_currency).
    계층 중 하나라도 없거나 폐쇄됐으면 값을 추측하지 않고 거부한다(FA-5
    `resolve_context`와 같은 fail-closed 원칙)."""
    sub_account = await entities.get_sub_account(tenant_id, sub_account_id)
    if sub_account is None or sub_account.closed_at is not None:
        raise AllocationTargetError(f"sub_account {sub_account_id}가 없거나 폐쇄됨")
    portfolio = await entities.get_portfolio(tenant_id, sub_account.portfolio_id)
    if portfolio is None or portfolio.closed_at is not None:
        raise AllocationTargetError(f"portfolio {sub_account.portfolio_id}가 없거나 폐쇄됨")
    fund = await entities.get_fund(tenant_id, portfolio.fund_id)
    if fund is None or fund.closed_at is not None:
        raise AllocationTargetError(f"fund {portfolio.fund_id}가 없거나 폐쇄됨")
    return _ResolvedTarget(
        owner_ref=sub_account.owner_ref,
        portfolio_id=portfolio.portfolio_id,
        fund_id=fund.fund_id,
        base_currency=fund.base_currency,
    )


async def _ensure_user_account(conn: asyncpg.Connection, code: str, currency: Currency) -> None:
    """`topup.py::_reconcile_ledger_with_projection`과 같은 프로비저닝
    패턴 — `ledger_account`/`ledger_balance` 행이 없으면 만든다(LC-9는
    미지 계정을 조용히 만들지 않으므로 이 책임은 호출자가 진다)."""
    negative_ok = allows_negative(code)
    await conn.execute(
        "INSERT INTO ledger_account (account_code, account_type, currency, allow_negative) "
        "VALUES ($1, $2, $3, $4) ON CONFLICT (account_code) DO NOTHING",
        code, account_type(code).value, currency.value, negative_ok,
    )
    await conn.execute(
        "INSERT INTO ledger_balance (account_id, allow_negative) "
        "SELECT account_id, $2 FROM ledger_account WHERE account_code = $1 "
        "ON CONFLICT (account_id) DO NOTHING",
        code, negative_ok,
    )


async def _post_allocation_entry(
    conn: asyncpg.Connection,
    *,
    tenant_id: UUID,
    order_id: UUID,
    side: str,
    allocation: SubAccountAllocation,
    entities: EntityRepository,
    journal: LedgerJournalRepository,
    balances: BalanceRepository,
    audit: AuditAppender,
    clock: Clock,
    trace_id: UUID,
    actor_subject_id: UUID | None,
) -> FillAllocationResult:
    target = await _resolve_target(entities, tenant_id, allocation.sub_account_id)
    fund_id, portfolio_id, currency = target.fund_id, target.portfolio_id, target.base_currency

    code = ua(target.owner_ref, LedgerUserSub.AVAILABLE)
    await _ensure_user_account(conn, code, currency)

    notional = round_to_quantum(allocation.quantity * allocation.average_price, _NOTIONAL_QUANTUM)
    debit_account, credit_account = (
        (code, PLATFORM_CASH_CLEARING) if side == "BUY" else (PLATFORM_CASH_CLEARING, code)
    )

    event = LedgerEvent(
        event_type=LedgerEventType.MANUAL_ADJUSTMENT,
        event_ref=f"fill_allocation:{order_id}:{allocation.sub_account_id}",
        tenant_id=tenant_id,
        actor_subject_id=actor_subject_id,
        trace_id=trace_id,
        amount=notional,
        currency=currency,
        parties={},
        extra={"debit_account": debit_account, "credit_account": credit_account},
        fund_id=fund_id,
        portfolio_id=portfolio_id,
    )
    entry = await post_entry(
        conn, event, journal=journal, balances=balances, audit=audit, clock=clock
    )

    await conn.execute(
        "INSERT INTO fill_allocation "
        "(order_id, sub_account_id, tenant_id, fund_id, portfolio_id, quantity, "
        " average_price, ledger_entry_id) "
        "VALUES ($1, $2, $3, $4, $5, $6, $7, $8) "
        "ON CONFLICT (order_id, sub_account_id) DO NOTHING",
        order_id,
        allocation.sub_account_id,
        tenant_id,
        fund_id,
        portfolio_id,
        allocation.quantity,
        allocation.average_price,
        entry.entry_id,
    )

    return FillAllocationResult(
        sub_account_id=allocation.sub_account_id,
        quantity=allocation.quantity,
        average_price=allocation.average_price,
        fund_id=fund_id,
        portfolio_id=portfolio_id,
        ledger_entry_id=entry.entry_id,
        replayed=entry.replayed,
    )


async def allocate_order_fills(
    conn: asyncpg.Connection,
    *,
    tenant_id: UUID,
    order_id: UUID,
    policy: AllocationPolicy,
    weight_targets: Sequence[WeightTarget] = (),
    manual_targets: Sequence[ManualTarget] = (),
    quantum: Decimal,
    price_quantum: Decimal,
    entities: EntityRepository,
    journal: LedgerJournalRepository,
    balances: BalanceRepository,
    audit: AuditAppender,
    clock: Clock,
    trace_id: UUID,
    actor_subject_id: UUID | None = None,
) -> tuple[FillAllocationResult, ...]:
    """`order_id`의 체결 전부를 조회해 `policy`로 sub_account별 수량을
    나누고, 부분체결 가중평균 단가를 부여한 뒤 sub_account마다 원장 분개
    + `fill_allocation` 기록을 남긴다. 호출자가 이미 연 `conn`/트랜잭션을
    그대로 쓴다(post_entry와 같은 계약 — 이 함수는 커밋/롤백을 결정하지
    않는다)."""
    fill_rows = await conn.fetch(
        "SELECT quantity, price FROM fills WHERE order_id = $1", order_id
    )
    if not fill_rows:
        raise NoFillsError(f"order_id={order_id}: 배분할 체결이 없음")
    side = await conn.fetchval("SELECT side FROM orders WHERE order_id = $1", order_id)
    if side is None:
        raise NoFillsError(f"order_id={order_id}: 주문을 찾을 수 없음")

    partial_fills = [PartialFill(quantity=r["quantity"], price=r["price"]) for r in fill_rows]
    total_quantity = sum((f.quantity for f in partial_fills), Decimal("0"))
    total_notional = sum((f.quantity * f.price for f in partial_fills), Decimal("0"))

    lines = allocate(
        policy,
        total_quantity,
        weight_targets=weight_targets,
        manual_targets=manual_targets,
        quantum=quantum,
    )
    average_price = blended_average_price(partial_fills, price_quantum)
    allocations = apply_average_price(
        lines,
        average_price,
        total_notional=total_notional,
        notional_quantum=_NOTIONAL_QUANTUM,
    )

    results = []
    for allocation in allocations:
        result = await _post_allocation_entry(
            conn,
            tenant_id=tenant_id,
            order_id=order_id,
            side=side,
            allocation=allocation,
            entities=entities,
            journal=journal,
            balances=balances,
            audit=audit,
            clock=clock,
            trace_id=trace_id,
            actor_subject_id=actor_subject_id,
        )
        results.append(result)
    return tuple(results)
