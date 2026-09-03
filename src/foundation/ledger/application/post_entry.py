"""LC-9 — 모든 원장 포스팅의 단일 경로.

Spec: docs/specs/L4_market_data_positions_ledger_v1.0.md#§5, §6, §9 LC-9.

§6(실패 모드) "C 감사 append 실패 → 포스팅 전체 롤백"과 §5 "한 커넥션,
한 트랜잭션"을 그대로 코드화한다. 순서: `ledger_control` 동결 확인
(fail-closed) → `event.extra` 안전성 확인(LC-17 결함 A, secret류 키·
비화이트리스트 키 거부) → 멱등 lookup(REPLAY/DIGEST_MISMATCH) → `lines_for`(LC-4,
내부에서 `check_balanced` 재확인) → 잔액 `FOR UPDATE` → 델타 적용을
`balance_rules.apply`(순수 검증)로 사전 검증 → 저널 append(DB write) →
잔액 실제 갱신(DB write) → `append_event_in`(같은 `conn`, DB write). 이
함수는 `conn`을 받기만 하고 자신의 트랜잭션을 열지 않는다 — 두 번째
커넥션을 획득하지 않는다(전수감사 §2 P1).

계정별 델타 부호: §4.4 "자산·비용은 차변 증가, 부채·수익은 대변 증가".
`PostingLine`은 방향(side)과 계정코드만 담고 부호가 반영된 `delta_balance`는
없으므로, 이 함수가 `chart_of_accounts.account_type`으로 계정 성격을 봐서
부호를 매긴다(`CLEARING`은 §4.4 표에서 credit만 받으므로 대변 증가로
취급 — `PLATFORM:PAYOUT_CLEARING`이 allow_negative=False로 시드되어 있어
credit-증가가 아니면 즉시 음수가 되어 깨진다, LC-6 시드 참고).
`ledger_balance.held` 컬럼은 이 리프가 건드리지 않는다(§4.4에서 HELD는
`USER:*:HELD` 같은 별도 계정코드로 표현되고, 그 계정의 `balance`가
갱신된다 — 행별 `held` 필드는 항상 델타 0으로 남겨둔다).

`BalanceRepository.apply`의 `expected_seq`는 `get_for_update`가 돌려준
현재 `last_entry_seq`이고(어댑터 LC-8b 설계: 낙관적 락 버전 카운터, 전역
분개 sequence_no가 아니다), `balance_rules.apply`(순수 사전 검증)의
`entry_seq`도 어댑터와 같은 관례(+1)로 맞춘다 — 전역 `sequence_no`는 저널
append 이후에야 알 수 있고, 사전 검증 자체는 그 값을 필요로 하지 않는다.

DIGEST_MISMATCH 시 `append_event_in`으로 DENIED 감사 이벤트를 남긴 *뒤에*
예외를 던진다 — 이 함수가 도는 트랜잭션이 결국 롤백되면 이 DENIED 행도
함께 사라진다. 호출자가 이 예외를 트랜잭션 경계 **안에서** 잡아 흡수하고
커밋해야 DENIED 감사가 남는다(105번 §5.1과 동일한 "커넥션은 호출자 것"
계약 — 이 함수가 스스로 커밋/롤백을 결정하지 않는다).
"""
from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from decimal import Decimal
from typing import Protocol
from uuid import UUID

import asyncpg

from src.foundation.evidence.domain.models import AuditEvent, Classification, Outcome
from src.foundation.evidence.domain.rules import (
    UnsafePayloadError,
    assert_safe_payload,
    compute_payload_hash,
)
from src.foundation.ledger.contracts.v1 import (
    EXTRA_ALLOWED_KEYS,
    AccountType,
    JournalEntryView,
    LedgerEvent,
    Side,
)
from src.foundation.ledger.domain import balance_rules, posting_rules
from src.foundation.ledger.domain.balance_rules import Balance
from src.foundation.ledger.domain.chart_of_accounts import account_type, allows_negative
from src.foundation.ledger.domain.hash_chain import lines_digest
from src.foundation.ledger.domain.idempotency import IdempotencyDigestMismatchError, idempotency_key
from src.foundation.ledger.ports.balance_repository import BalanceRepository
from src.foundation.ledger.ports.journal_repository import LedgerJournalRepository

Clock = Callable[[], datetime]

_DEBIT_NORMAL_TYPES = frozenset({AccountType.ASSET, AccountType.EXPENSE})


class LedgerWriteFrozenError(Exception):
    """`ledger_control.write_frozen = true` — 무결성 위반 감지 후 원장 쓰기
    전면 차단(fail-closed, §4.4). 관리자 2인 승인 해동 전까지 재시도 불가."""


class LedgerEventExtraRejectedError(ValueError):
    """`event.extra`에 secret류 키(§8.3 LC-17 결함 A) 또는 이 사건 타입의
    화이트리스트(`contracts.v1.EXTRA_ALLOWED_KEYS`)에 없는 키가 있어
    포스팅 전체를 거부했다. 예외 삼킴 없이 던져지며, 던지기 전에 DENIED
    감사 이벤트가 남는다(`_deny_unsafe_extra`)."""

    def __init__(self, detail: str) -> None:
        super().__init__(detail)
        self.detail = detail


class AuditAppender(Protocol):
    """`AuditEventRepository`의 `append_event_in`만 쓴다 — `record_command_event`
    (별도 conn 획득)를 쓰지 않는 이유는 모듈 docstring 참고."""

    async def append_event_in(
        self,
        conn: asyncpg.Connection,
        *,
        tenant_id: UUID | None,
        aggregate_type: str,
        aggregate_id: UUID,
        aggregate_revision: int | None,
        action: str,
        outcome: Outcome,
        actor_subject_id: UUID | None,
        trace_id: UUID,
        payload_hash: str,
        payload: dict[str, object],
        classification: Classification,
    ) -> AuditEvent: ...


def _signed_delta(side: Side, code: str, amount: Decimal) -> Decimal:
    debit_increases = account_type(code) in _DEBIT_NORMAL_TYPES
    increases = (side is Side.DEBIT) == debit_increases
    return amount if increases else -amount


async def _assert_not_frozen(conn: asyncpg.Connection) -> None:
    frozen = await conn.fetchval("SELECT write_frozen FROM ledger_control WHERE id = 1 FOR SHARE")
    if frozen:
        raise LedgerWriteFrozenError("ledger_control.write_frozen=true — 원장 쓰기가 동결됐습니다.")


async def _deny_unsafe_extra(
    conn: asyncpg.Connection,
    audit: AuditAppender,
    event: LedgerEvent,
    *,
    detail: str,
) -> None:
    payload: dict[str, object] = {
        "event_ref": event.event_ref,
        "rejected_extra_keys": sorted(event.extra.keys()),
        "reason": detail,
    }
    assert_safe_payload(payload)
    await audit.append_event_in(
        conn,
        tenant_id=event.tenant_id,
        aggregate_type="ledger_event_extra",
        aggregate_id=event.trace_id,
        aggregate_revision=None,
        action=event.event_type.value,
        outcome=Outcome.DENIED,
        actor_subject_id=event.actor_subject_id,
        trace_id=event.trace_id,
        payload_hash=compute_payload_hash(payload),
        payload=payload,
        classification=Classification.INTERNAL,
    )


async def _assert_extra_safe(
    conn: asyncpg.Connection, audit: AuditAppender, event: LedgerEvent
) -> None:
    """LC-17 결함 A 수정. §8.3 DoD: `event.extra`에 secret류 키나 이 사건
    타입이 실제로 읽지 않는(비화이트리스트) 키가 실리면 저장 전에 거부한다.
    `event.extra`가 아직 journal entry로 이어지기 전이라 `aggregate_id`로
    삼을 entry_id가 없으므로 요청과 1:1인 `trace_id`를 대신 쓴다."""
    try:
        assert_safe_payload(event.extra)
    except UnsafePayloadError as exc:
        await _deny_unsafe_extra(conn, audit, event, detail=str(exc))
        raise LedgerEventExtraRejectedError(str(exc)) from exc

    allowed = EXTRA_ALLOWED_KEYS.get(event.event_type, frozenset())
    disallowed = sorted(set(event.extra) - allowed)
    if disallowed:
        detail = (
            f"{event.event_type.value}: extra에 허용되지 않은 키 {disallowed!r} "
            f"(허용: {sorted(allowed)!r})"
        )
        await _deny_unsafe_extra(conn, audit, event, detail=detail)
        raise LedgerEventExtraRejectedError(detail)


async def _deny_digest_mismatch(
    conn: asyncpg.Connection,
    audit: AuditAppender,
    event: LedgerEvent,
    *,
    key: str,
    existing_entry_id: UUID,
    existing_digest: str,
    new_digest: str,
) -> None:
    payload: dict[str, object] = {
        "idempotency_key": key,
        "existing_entry_id": str(existing_entry_id),
        "existing_lines_digest": existing_digest,
        "rejected_lines_digest": new_digest,
    }
    assert_safe_payload(payload)
    await audit.append_event_in(
        conn,
        tenant_id=event.tenant_id,
        aggregate_type="ledger_journal_entry",
        aggregate_id=existing_entry_id,
        aggregate_revision=None,
        action=event.event_type.value,
        outcome=Outcome.DENIED,
        actor_subject_id=event.actor_subject_id,
        trace_id=event.trace_id,
        payload_hash=compute_payload_hash(payload),
        payload=payload,
        classification=Classification.INTERNAL,
    )


async def post_entry(
    conn: asyncpg.Connection,
    event: LedgerEvent,
    *,
    journal: LedgerJournalRepository,
    balances: BalanceRepository,
    audit: AuditAppender,
    clock: Clock,
) -> JournalEntryView:
    await _assert_not_frozen(conn)
    await _assert_extra_safe(conn, audit, event)

    key = idempotency_key(event)
    lines = posting_rules.lines_for(event)
    balance_rules.check_balanced(lines)
    new_digest = lines_digest(lines)

    existing = await journal.find_by_idempotency_key(conn, key)
    if existing is not None:
        if existing.lines_digest != new_digest:
            await _deny_digest_mismatch(
                conn,
                audit,
                event,
                key=key,
                existing_entry_id=existing.entry_id,
                existing_digest=existing.lines_digest,
                new_digest=new_digest,
            )
            raise IdempotencyDigestMismatchError(key)
        return existing.model_copy(update={"replayed": True})

    account_codes = sorted({line.account_code for line in lines})
    current = await balances.get_for_update(conn, account_codes)

    deltas: dict[str, Decimal] = {}
    for line in lines:
        deltas[line.account_code] = deltas.get(
            line.account_code, Decimal("0")
        ) + _signed_delta(line.side, line.account_code, line.amount)

    for code, delta in deltas.items():
        view = current[code]
        balance_rules.apply(
            Balance(
                account_code=code,
                balance=view.balance,
                held=view.held,
                currency=view.currency,
                allow_negative=allows_negative(code),
                last_entry_seq=view.last_entry_seq,
            ),
            delta_balance=delta,
            delta_held=Decimal("0"),
            entry_seq=view.last_entry_seq + 1,
        )

    entry_view = await journal.append(conn, event, lines)
    if entry_view.replayed:
        # 사전체크는 최적화일 뿐이다 — 두 요청이 그 체크를 모두 통과한 뒤
        # `get_for_update`(행 잠금)에서 직렬화되면, 나중 트랜잭션은 이미
        # 최신 잔액으로 사전검증을 통과해버린다. 적용 스킵의 유일한 근거는
        # `journal.append`가 advisory lock 하에서 내린 이 판정뿐이다(LC-9).
        return entry_view

    for code, delta in deltas.items():
        await balances.apply(conn, code, delta, Decimal("0"), current[code].last_entry_seq)

    payload: dict[str, object] = {
        "event_ref": event.event_ref,
        "entry_id": str(entry_view.entry_id),
        "sequence_no": entry_view.sequence_no,
    }
    assert_safe_payload(payload)
    await audit.append_event_in(
        conn,
        tenant_id=event.tenant_id,
        aggregate_type="ledger_journal_entry",
        aggregate_id=entry_view.entry_id,
        aggregate_revision=None,
        action=event.event_type.value,
        outcome=Outcome.SUCCESS,
        actor_subject_id=event.actor_subject_id,
        trace_id=event.trace_id,
        payload_hash=compute_payload_hash(payload),
        payload=payload,
        classification=Classification.INTERNAL,
    )

    return entry_view
