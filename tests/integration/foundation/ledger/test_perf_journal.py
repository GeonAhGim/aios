"""LC-17 계약·성능 — 저널 append p95 < 30ms(100회, 실 DB).

Spec: docs/specs/L4_market_data_positions_ledger_v1.0.md#§8.4, §9 LC-17
("저널 append p95 < 30ms(100회)" — "포스팅 p95 < 50ms(200회)"와는 별도
항목이라 여기서는 측정 범위를 좁힌다).

측정 대상은 `PostgresJournalRepository.append()` 단독이다 — `append()`는
저널 3테이블(`ledger_journal_entry`·`ledger_posting_line`·
`foundation_audit_event`)에만 쓰고 `ledger_balance`는 건드리지 않으므로
(`post_entry.py` 모듈 docstring 참고), 계정 `FOR UPDATE` 잠금 경합 없이
순수 저널 쓰기 경로만 잰다. `tests/integration/test_event_bus_latency_
benchmark.py`가 남긴 선례와 같은 이유로 `pytest-benchmark` 플러그인
대신 수동 p95 측정을 쓴다 — 그 파일의 실측대로 반복측정 모델은 대상
호출과 무관한 하네스 오버헤드(여기서는 커넥션 획득·해제)를 섞어버릴 수
있어, 매 회 실제 사용 패턴(짧은 트랜잭션 하나)을 그대로 재현하는 편이
더 정직한 측정이다.

실결함(task-614/LC-17 발견, needs_decision): 이 TEST_DATABASE_URL
(aios_test_backend_3, localhost)에서 3회 반복 실측한 p95는
34.8ms/53.3ms/48.5ms — 목표(30ms)를 항상 초과한다. 당시 `append()` 한
번이 순차 DB 왕복을 약 7회 만들었다(저널용 `pg_advisory_xact_lock` 1회 +
"마지막 저널행" SELECT 1회 + 감사용 `pg_advisory_xact_lock` 1회 + "마지막
감사행" SELECT 1회 + 감사 INSERT 1회 + 저널 INSERT 1회 + 분개행 INSERT
2회, 2행 엔트리 기준).

task-627(LC-17 결함 B)에서 `postgres_journal_repository.append()`가 직접
발행하는 왕복을 줄였다 — 멱등키 조회·마지막 행 조회·계좌코드 해석 3회를
CTE/LATERAL 쿼리 1회로 묶고, 분개행 INSERT를 행마다 대신 멀티행 VALUES
1회로 묶었다. 감사 이벤트 체인(`foundation_audit_event`, 이 모듈이 손대지
않는 별도 컴포넌트)의 advisory lock·마지막 행 SELECT·INSERT 3회는 그대로
남는다 — FK 제약(`ledger_journal_entry.audit_event_id`) 때문에 저널
INSERT 전에 반드시 거쳐야 하고, 이 리프의 파일 범위(`postgres_journal_
repository.py`) 밖이다.

이 leaf(task-627)의 목표는 "30ms 통과"가 아니라 "왕복 축소 + 실측치
확보"다(decision 참고 — 이 localhost 실측 기준으로 임계를 재조정하지
않고 CI 환경 측정 전까지 보류). 개선 전/후 실측치는 task-627 tests
필드에 남겨 뒀다. 아래 단언은 스펙이 요구하는 목표를 그대로 걸고, 실패
자체를 `xfail(strict=True)`로 고정해 조용히 넘기지 않는다."""
from __future__ import annotations

import time
from decimal import Decimal
from uuid import UUID, uuid4

import pytest

from src.data.models.base import Currency
from src.foundation.ledger.adapters.postgres_journal_repository import PostgresJournalRepository
from src.foundation.ledger.contracts.v1 import LedgerEvent, LedgerEventType, UserSub
from src.foundation.ledger.domain import posting_rules
from src.foundation.ledger.domain.chart_of_accounts import user_account as ua
from tests.integration.conftest import create_test_user

_SAMPLE_COUNT = 100
_TARGET_P95_MS = 30.0


async def _seed_user_available_account(pool, user_id: UUID) -> None:
    code = ua(user_id, UserSub.AVAILABLE)
    async with pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO ledger_account (account_code, account_type, currency, allow_negative) "
            "VALUES ($1, 'LIABILITY', 'KRW', FALSE) ON CONFLICT (account_code) DO NOTHING",
            code,
        )
        await conn.execute(
            "INSERT INTO ledger_balance (account_id, allow_negative, last_entry_seq) "
            "SELECT account_id, FALSE, 0 FROM ledger_account WHERE account_code = $1 "
            "ON CONFLICT (account_id) DO NOTHING",
            code,
        )


@pytest.mark.xfail(
    strict=True,
    reason=(
        "실결함(task-614/LC-17 발견, needs_decision): task-627에서 "
        "journal.append()의 직접 왕복(멱등조회+마지막행+계좌해석 3회→1회, "
        "분개행 INSERT N회→1회)을 줄였지만 감사 이벤트 체인(별도 컴포넌트) "
        "고유의 lock+SELECT+INSERT 3회는 그대로 남아 이 환경(localhost)에서 "
        "여전히 p95가 목표(30ms)를 초과할 수 있다. 모듈 docstring 참고 — "
        "임계 재조정은 CI 환경 실측 전까지 보류(decision, task-627)."
    ),
)
async def test_journal_append_p95_under_30ms(pool) -> None:
    journal = PostgresJournalRepository(pool)
    user_ids = [await create_test_user(pool) for _ in range(_SAMPLE_COUNT)]
    for user_id in user_ids:
        await _seed_user_available_account(pool, user_id)

    latencies_ms: list[float] = []
    for user_id in user_ids:
        event = LedgerEvent(
            event_type=LedgerEventType.TOPUP_CONFIRMED,
            event_ref=f"perf:topup:{uuid4()}",
            tenant_id=None,
            actor_subject_id=None,
            trace_id=uuid4(),
            amount=Decimal("1.00"),
            currency=Currency.KRW,
            parties={"user": user_id},
            extra={},
        )
        lines = posting_rules.lines_for(event)

        started = time.perf_counter()
        async with pool.acquire() as conn, conn.transaction():
            await journal.append(conn, event, lines)
        latencies_ms.append((time.perf_counter() - started) * 1000)

    latencies_ms.sort()
    p50_ms = latencies_ms[int(len(latencies_ms) * 0.50)]
    p95_ms = latencies_ms[int(len(latencies_ms) * 0.95)]

    print(
        f"\nledger journal append latency: p50={p50_ms:.3f}ms p95={p95_ms:.3f}ms "
        f"(n={len(latencies_ms)})"
    )

    assert len(latencies_ms) == _SAMPLE_COUNT
    assert p95_ms < _TARGET_P95_MS, (
        f"저널 append p95({p95_ms:.3f}ms)가 목표({_TARGET_P95_MS}ms)를 초과했습니다."
    )
