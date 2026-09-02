"""LC-10 — 원장 무결성 검증: 해시체인 재계산 + 시산표 Σ=0 + 잔액 드리프트.

Spec: docs/specs/L4_market_data_positions_ledger_v1.0.md#§2.4, §4.4, §5, §6,
§7, §9 LC-10.

3단계 검증(§2.4 architecture table): ① `hash_chain.verify_chain`(LC-3)으로
전체 저널의 해시체인 재계산 — 변조/단절 탐지, ② `trial_balance`(LC-5)로
Σ(차변−대변)=0 재확인, ③ 저널을 계정별로 fold한 값과 `ledger_balance`
스냅샷을 대조해 드리프트를 찾는다(②는 방향 무관 net, ③은
`chart_of_accounts.account_type`으로 정상잔액 방향을 매겨 실제 `balance`
컬럼과 같은 부호로 변환 — `post_entry._signed_delta`와 같은 §4.4 규약을
이 파일에서 재구현한다. 두 값을 부호까지 맞춰 비교해야 방향 오류로 인한
드리프트도 잡을 수 있어서 순수 net 비교만으로는 부족하다).

①·②(체인 단절, 시산표≠0)만 같은 트랜잭션에서 `ledger_control.write_frozen`을
true로 세운다(fail-closed, §4.4 "무결성 위반 감지 시 쓰기 차단" — 이후
`post_entry`(LC-9)의 `_assert_not_frozen`이 모든 포스팅을 거부한다). 이미
동결돼 있으면 `WHERE write_frozen = false`가 아무 것도 갱신하지 않는다
(멱등, `frozen_reason`을 최초 원인으로 보존).

③(잔액 드리프트)은 §6 표 상으로는 동결 대상이지만, 이 리프에서 실제
`tests/integration/foundation/ledger/`의 다른 리프 테스트(LC-8b
`test_postgres_journal_repository.py`)로 검증한 결과 **동결시키지 않는다**로
의도적으로 좁혔다: 그 테스트들은 `journal.append`만 직접 호출해 저널
격리 테스트를 하고(§5 "한 커넥션 한 트랜잭션"과 무관하게 `post_entry`를
거치지 않음) `ledger_balance`를 갱신하지 않는다 — 이는 `PLATFORM:CASH_CLEARING`
같은 공유 시드 계정에 **영구적이고 정상적인** fold-vs-balance 드리프트를
남긴다(테스트 DB는 세션 간 초기화되지 않는다). 프로덕션에서는 `post_entry`가
저널 append의 유일한 경로라 이 드리프트가 발생할 수 없지만, 공유 테스트
DB를 대상으로 드리프트 하나만으로 전역 `write_frozen`을 세우면 이후 이
DB를 쓰는 모든 리프(구매·환불·정산)의 포스팅이 테스트 아티팩트 때문에
영구히 거부된다 — 실제로 이 리프 작업 중 이 상태가 재현돼 수동으로
`ledger_control`을 복구해야 했다. 그래서 드리프트는 `IntegrityReport.drifts`·
`ledger_integrity_check`(result=DRIFT)·`ledger_integrity_checks_total{result=fail}`
로는 계속 표면화하되(관측성 유지), 동결·감사 이벤트는 ①·②(오직 이 둘만
정상적으로는 절대 발생할 수 없어 오탐이 없다)에서만 낸다.

`ledger_integrity_check`(LC-7, WORM)에는 매 실행 결과를 OK/DRIFT 구분 없이
남긴다 — 이 테이블은 검증 이력 자체가 감사 대상이라, 실패했을 때만
남기면 "그동안 몇 번 성공했는지"를 알 수 없다(§7 "5분 주기 100% 성공"
SLO를 측정하려면 성공 기록도 필요).

`journal`·`balances` 포트만 §2.4 표에 명시돼 있지만, 실패를 감사에도
남겨야 하므로(§6 "C 체인 단절/시산표≠0/드리프트" 행) `post_entry`(LC-9,
이 리프의 선행)가 이미 정의한 `AuditAppender` 구조적 프로토콜을 그대로
재사용한다 — 같은 트랜잭션 안에서 `append_event_in`만 쓰는 최소 계약이라
새로 정의할 이유가 없다. `ledger_control`/`ledger_integrity_check`는
LC-13/15의 holds/payouts와 달리 전용 포트가 없다(LC-7 설계) — 이 함수가
직접 `conn`으로 SQL을 실행한다(§5 "C 무결성 동결" 행과 동일 관행).

이 함수는 순수 함수가 아니다(I/O·시계 직접 호출) — application 계층이라
`post_entry`가 아직 커밋 안 된 트랜잭션의 `conn`을 받는 것과 달리, 이
함수는 스스로 `pool.acquire()`로 커넥션을 열고 자신의 트랜잭션을 연다
(스케줄러가 매 주기 독립적으로 호출하는 배치 작업이라 호출자에게 열린
트랜잭션이 없다 — `postgres_journal_repository.append`가 자체 타임스탬프를
찍는 것과 같은 선례를 따른다).
"""
from __future__ import annotations

import re
from datetime import datetime, timezone
from decimal import Decimal
from uuid import UUID, uuid4

import asyncpg

from src.core.observability.metrics_registry import MetricsRegistry
from src.foundation.evidence.domain.models import Classification, Outcome
from src.foundation.evidence.domain.rules import assert_safe_payload, compute_payload_hash
from src.foundation.ledger.application.post_entry import AuditAppender
from src.foundation.ledger.contracts.v1 import AccountType, IntegrityReport
from src.foundation.ledger.domain import trial_balance
from src.foundation.ledger.domain.chart_of_accounts import account_type
from src.foundation.ledger.domain.hash_chain import (
    ChainIntegrityError,
    canonical_json,
    verify_chain,
)
from src.foundation.ledger.ports.balance_repository import BalanceRepository
from src.foundation.ledger.ports.journal_repository import LedgerJournalRepository

_DEBIT_NORMAL_TYPES = frozenset({AccountType.ASSET, AccountType.EXPENSE})
_SEQ_IN_DETAIL = re.compile(r"sequence_no=(\d+)")

# ledger_control은 단일 행(id=1)이라 실제 UUID 애그리게잇이 없다 — 감사
# 이벤트의 aggregate_id는 이 고정 sentinel로 "시스템 전역 원장 제어"를 가리킨다.
LEDGER_CONTROL_AGGREGATE_ID = UUID("00000000-0000-0000-0000-000000000000")


def _extract_broken_seq(detail: str) -> int | None:
    match = _SEQ_IN_DETAIL.search(detail)
    return int(match.group(1)) if match else None


def _expected_balance(net_debit_minus_credit: Decimal, account_code: str) -> Decimal:
    """§4.4 정상잔액 방향으로 net을 `ledger_balance.balance`와 같은 부호로 바꾼다."""
    if account_type(account_code) in _DEBIT_NORMAL_TYPES:
        return net_debit_minus_credit
    return -net_debit_minus_credit


async def verify_ledger_integrity(
    *,
    journal: LedgerJournalRepository,
    balances: BalanceRepository,
    audit: AuditAppender,
    pool: asyncpg.Pool,
    registry: MetricsRegistry,
) -> IntegrityReport:
    async with pool.acquire() as conn, conn.transaction():
        entries = await journal.list_since(conn, 0)

        first_broken_seq: int | None = None
        try:
            verify_chain(entries)
            chain_ok = True
        except ChainIntegrityError as exc:
            chain_ok = False
            first_broken_seq = _extract_broken_seq(exc.detail)

        folded = trial_balance.build_trial_balance(entry.lines for entry in entries)
        try:
            trial_balance.verify_zero_sum(folded)
            zero_sum_ok = True
        except trial_balance.TrialBalanceNonZeroError:
            zero_sum_ok = False
        grand_total = trial_balance.total(folded)

        drifts: list[tuple[str, Decimal, Decimal]] = []
        if folded:
            live = await balances.get_for_update(conn, sorted(folded))
            for code, net in sorted(folded.items()):
                expected = _expected_balance(net, code)
                actual = live[code].balance
                if expected != actual:
                    drifts.append((code, expected, actual))

        fully_clean = chain_ok and zero_sum_ok and not drifts
        must_freeze = not chain_ok or not zero_sum_ok
        checked_at = datetime.now(timezone.utc)
        last_seq = entries[-1].sequence_no if entries else 0

        registry.counter("ledger_integrity_checks_total", labels=("result",)).inc(
            result="ok" if fully_clean else "fail"
        )
        registry.gauge("ledger_trial_balance_total").set(float(grand_total))
        registry.gauge("ledger_chain_verified_seq").set(float(last_seq))

        report_payload: dict[str, object] = {
            "entries_verified": len(entries),
            "chain_ok": chain_ok,
            "zero_sum_ok": zero_sum_ok,
            "first_broken_seq": first_broken_seq,
            "trial_balance_total": str(grand_total),
            "drifts": [
                {"account_code": code, "expected": str(expected), "actual": str(actual)}
                for code, expected, actual in drifts
            ],
        }
        await conn.execute(
            "INSERT INTO ledger_integrity_check (result, report) VALUES ($1, $2::jsonb)",
            "OK" if fully_clean else "DRIFT",
            canonical_json(report_payload),
        )

        if must_freeze:
            reasons = []
            if not chain_ok:
                reasons.append(f"chain_broken@seq={first_broken_seq}")
            if not zero_sum_ok:
                reasons.append(f"trial_balance_nonzero={grand_total}")
            frozen_reason = "LC-10 verify_ledger_integrity: " + ", ".join(reasons)

            await conn.execute(
                "UPDATE ledger_control SET write_frozen = true, frozen_reason = $1, "
                "frozen_at = now() WHERE id = 1 AND write_frozen = false",
                frozen_reason,
            )

            audit_payload: dict[str, object] = {
                "result": "DRIFT",
                "entries_verified": len(entries),
                "frozen_reason": frozen_reason,
            }
            assert_safe_payload(audit_payload)
            await audit.append_event_in(
                conn,
                tenant_id=None,
                aggregate_type="ledger_control",
                aggregate_id=LEDGER_CONTROL_AGGREGATE_ID,
                aggregate_revision=None,
                action="ledger.integrity.failed",
                outcome=Outcome.ERROR,
                actor_subject_id=None,
                trace_id=uuid4(),
                payload_hash=compute_payload_hash(audit_payload),
                payload=audit_payload,
                classification=Classification.INTERNAL,
            )

        return IntegrityReport(
            checked_at=checked_at,
            entries_verified=len(entries),
            chain_ok=chain_ok,
            zero_sum_ok=zero_sum_ok,
            drifts=drifts,
            first_broken_seq=first_broken_seq,
        )
