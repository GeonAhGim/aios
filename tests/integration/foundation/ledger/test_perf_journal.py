"""LC-17 계약·성능 — 저널 append p95, 환경 정규화 + 왕복수 회귀 가드(실 DB).

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

실결함(task-614/LC-17 발견) → task-627에서 `postgres_journal_repository.
append()`가 직접 발행하는 왕복을 줄였다 — 멱등키 조회·마지막 행 조회·
계좌코드 해석 3회를 CTE/LATERAL 쿼리 1회로 묶고, 분개행 INSERT를 행마다
대신 멀티행 VALUES 1회로 묶었다. 감사 이벤트 체인(`foundation_audit_
event`, 이 모듈이 손대지 않는 별도 컴포넌트)의 advisory lock·마지막 행
SELECT·INSERT 3회는 그대로 남는다 — FK 제약(`ledger_journal_entry.
audit_event_id`) 때문에 저널 INSERT 전에 반드시 거쳐야 하고, 이 리프의
파일 범위(`postgres_journal_repository.py`) 밖이다. 결과: `append()` 1회는
순차 DB 왕복 7회를 쓴다(저널 lock 1 + CTE 1 + 감사 lock/SELECT/INSERT 3 +
저널 INSERT 1 + 분개행 멀티행 INSERT 1).

task-627 당시에는 이 왕복수 기준의 절대 임계(30ms) 충족 여부를 이
localhost 환경 실측(34.8/53.3/48.5ms)으로는 판단할 수 없어 `xfail(strict=
True)`로 고정해 뒀다. task-920(CI 적색 진단): CI 로그는 p95=15.97ms로
30ms 미만인데도 이 테스트가 FAILED로 보고됐다 — 실패 지점은 라인 122의
`assert p95_ms < _TARGET_P95_MS`가 아니라, 그 단언이 예상대로 *통과*하며
`xfail(strict=True)` 아래에서 "예기치 않게 성공"(XPASS)한 것 자체가
strict 모드에서 실패로 보고되는 pytest 동작이다(왕복 축소가 이 CI
환경에서는 이미 목표를 달성했다는 뜻 — 코드 결함이 아니라 낡은 xfail
고정이 CI 개선을 실패로 오보한 것). task-822(`test_perf_journal_append.
py`, LB-11)와 동일한 근본 원인·동일한 decision(c)이라 같은 처방을
적용한다: 절대 임계(30ms)를 xfail로 숨기는 대신, 이 환경의 기준 DB
왕복비용(rt) 대비 p95 < max(30ms, k*rt)로 정규화하고, 왕복 수 자체를
asyncpg 쿼리 로거로 세어 <= 7(위 계산)을 단언하는 구조 회귀 가드를 더한다
— 이러면 이 환경이 원래 느려서 30ms를 못 채우는 경우와 코드가 왕복을
늘려 실제로 느려진 경우를 구분해서 잡는다. src(postgres_journal_
repository.py)는 무수정이다(왕복 축소는 이미 37a5375로 끝났고, 계약·
동작을 바꾸지 않는다)."""
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
_ROUND_TRIP_MULTIPLIER = 9
_MAX_SEQUENTIAL_ROUND_TRIPS = 7
_BASELINE_WARMUP = 5
_BASELINE_SAMPLE_COUNT = 50


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


async def _measure_baseline_round_trip_p95_ms(pool) -> float:
    """이 환경의 기준 DB 왕복비용(pool.acquire + BEGIN/COMMIT + SELECT 1) p95.

    journal.append 자체와 무관한, 이 실행환경(네트워크/디스크)의 순수 왕복
    비용만 재기 위한 대조군이다 — 워밍업 5회를 버려 최초 커넥션 수립
    비용(TLS/인증 등)이 섞이지 않게 한다."""
    samples_ms: list[float] = []
    for _ in range(_BASELINE_WARMUP + _BASELINE_SAMPLE_COUNT):
        started = time.perf_counter()
        async with pool.acquire() as conn, conn.transaction():
            await conn.fetchval("SELECT 1")
        samples_ms.append((time.perf_counter() - started) * 1000)

    samples_ms = samples_ms[_BASELINE_WARMUP:]
    samples_ms.sort()
    return samples_ms[int(len(samples_ms) * 0.95)]


def _topup_event(user_id: UUID) -> LedgerEvent:
    return LedgerEvent(
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


async def _count_append_round_trips(pool, journal: PostgresJournalRepository) -> int:
    """journal.append() 1회가 소비하는 순차 DB 왕복 수(구조 회귀 가드).

    측정 전 같은 커넥션으로 한 번 워밍업 호출을 먼저 흘려보낸다 — asyncpg는
    처음 보는 커넥션에서 커스텀 타입(여기서는 `foundation_audit_event`의
    enum·jsonb 컬럼)을 처음 쓸 때 코덱을 알아내려고 내부 조회(jit 설정·
    `typeinfo_tree` 등)를 몇 회 더 보낸다 — 이건 그 커넥션의 평생 1회성
    드라이버 오버헤드지 `journal.append()` 자체가 매 호출 내는 왕복이
    아니므로, 워밍업으로 먼저 흡수시켜야 이 함수가 실제 애플리케이션 왕복
    수만 잰다."""
    warmup_user_id = await create_test_user(pool)
    await _seed_user_available_account(pool, warmup_user_id)
    counted_user_id = await create_test_user(pool)
    await _seed_user_available_account(pool, counted_user_id)

    queries: list[str] = []

    def _log(record: object) -> None:
        queries.append(getattr(record, "query", ""))

    async with pool.acquire() as conn:
        warmup_event = _topup_event(warmup_user_id)
        async with conn.transaction():
            await journal.append(conn, warmup_event, posting_rules.lines_for(warmup_event))

        event = _topup_event(counted_user_id)
        lines = posting_rules.lines_for(event)
        async with conn.transaction():
            conn.add_query_logger(_log)
            try:
                await journal.append(conn, event, lines)
            finally:
                conn.remove_query_logger(_log)

    return len(queries)


@pytest.mark.perf
async def test_journal_append_p95_under_30ms(pool) -> None:
    """§9 LC-17 30ms는 운영 목표이며 CI는 환경 정규화 + 왕복수 상한으로
    회귀만 잡는다(task-822/LB-11과 동일한 decision(c), task-920)."""
    journal = PostgresJournalRepository(pool)

    baseline_p95_ms = await _measure_baseline_round_trip_p95_ms(pool)
    round_trip_count = await _count_append_round_trips(pool, journal)

    user_ids = [await create_test_user(pool) for _ in range(_SAMPLE_COUNT)]
    for user_id in user_ids:
        await _seed_user_available_account(pool, user_id)

    latencies_ms: list[float] = []
    for user_id in user_ids:
        event = _topup_event(user_id)
        lines = posting_rules.lines_for(event)

        started = time.perf_counter()
        async with pool.acquire() as conn, conn.transaction():
            await journal.append(conn, event, lines)
        latencies_ms.append((time.perf_counter() - started) * 1000)

    latencies_ms.sort()
    p50_ms = latencies_ms[int(len(latencies_ms) * 0.50)]
    p95_ms = latencies_ms[int(len(latencies_ms) * 0.95)]
    normalized_target_ms = max(_TARGET_P95_MS, _ROUND_TRIP_MULTIPLIER * baseline_p95_ms)

    print(
        f"\nledger journal append latency: p50={p50_ms:.3f}ms p95={p95_ms:.3f}ms "
        f"(n={len(latencies_ms)}); "
        f"baseline round-trip p95={baseline_p95_ms:.3f}ms (n={_BASELINE_SAMPLE_COUNT}); "
        f"normalized target={normalized_target_ms:.3f}ms "
        f"(max({_TARGET_P95_MS}, {_ROUND_TRIP_MULTIPLIER}*rt)); "
        f"sequential DB round trips={round_trip_count} (max={_MAX_SEQUENTIAL_ROUND_TRIPS})"
    )

    assert len(latencies_ms) == _SAMPLE_COUNT
    assert round_trip_count <= _MAX_SEQUENTIAL_ROUND_TRIPS, (
        f"journal append 순차 DB 왕복 수({round_trip_count})가 상한"
        f"({_MAX_SEQUENTIAL_ROUND_TRIPS})을 초과했습니다 — 왕복 수 회귀입니다."
    )
    assert p95_ms < normalized_target_ms, (
        f"저널 append p95({p95_ms:.3f}ms)가 환경 정규화 목표"
        f"({normalized_target_ms:.3f}ms)를 초과했습니다."
    )
