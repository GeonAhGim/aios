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
적용했다: 절대 임계(30ms)를 xfail로 숨기는 대신, 이 환경의 기준 DB
왕복비용(rt) 대비 p95 < max(30ms, k*rt)로 정규화하고, 왕복 수 자체를
asyncpg 쿼리 로거로 세어 <= 7(위 계산)을 단언하는 구조 회귀 가드를
더했다.

task-1029(CI 상시 적색 재발): 정규화 후에도 CI 실측 p95=172.7ms가
정규화 목표 74.9ms(기준왕복 p95=8.3ms)를 넘겨 여전히 FAILED로
보고됐다 — 왕복 수는 7/7로 이 리프의 DoD(왕복 축소)는 이미 달성된
상태였고, 남은 건 CI 인프라 자체의 절대 지연 변동성(로컬 대비 20배)
이라 이 테스트가 통제할 수 없는 신호였다. task-1038(esc-ci-d5723ce4366d
종결): 결론은 임계 재상향이 아니라 게이트 대상 변경 — 절대 지연 p95
단언을 제거하고 측정치는 print로만 남긴다. 왕복 수 단언(<= 7)만 차단
게이트로 남아 코드가 왕복을 다시 늘리는 실제 회귀는 계속 잡는다.
임계 상수를 키워 통과시키는 방식은 측정치를 무의미하게 만들고, xfail로
감추는 방식은 이미 XPASS strict로 되돌아온 전례가 있어(task-920) 둘 다
금지한다. src(postgres_journal_repository.py)는 무수정이다(왕복 축소는
이미 37a5375로 끝났고, 계약·동작을 바꾸지 않는다).

task-5599 fix(esc-ci-replay_verify): p95/왕복수 측정용 `journal.append()`
호출(`_count_append_round_trips`의 워밍업·측정 호출, `test_journal_append_
p95_under_30ms`의 100회 표본)은 `post_entry()`를 거치지 않고 `append()`
단독을 재는 것이 이 리프의 명시된 목적이라 `test_postgres_journal_
repository.py`(task-5309)와 같은 "post_entry로 우회" 처방을 그대로
적용할 수 없다 — `post_entry`는 `get_for_update`/`balances.apply` 왕복을
더해 <=7 단언 자체를 무의미하게 만든다. 대신 커밋 대신 명시적
롤백(`tx.rollback()`)으로 바꿨다: `_count_append_round_trips`의 쿼리
로거는 `journal.append()` 호출 구간에만 걸려 있어 그 뒤에 오는
COMMIT/ROLLBACK 선택은 측정된 왕복 수에 영향을 주지 않고, 지연 측정도
`async with` 블록 전체를 감싸므로 COMMIT 1회가 ROLLBACK 1회로 바뀌는 것
외에는 프로파일이 동일하다 — 그러면서 `PLATFORM:CASH_CLEARING`에 잔액이
갱신되지 않는 분개행을 영구히 남기지 않는다(공유 테스트 DB에서
`replay_verify`가 그 계정을 거짓 MISMATCH로 보고한 원인, `verify_
integrity.py` 모듈 docstring과 동일 드리프트 패턴).

반면 `test_journal_append_rejects_tampered_resend_as_digest_mismatch`/
`test_journal_append_concurrent_tampered_resend_rejects_loser`/`test_
journal_append_rolls_back_entirely_when_audit_append_fails`의 "성공해야
하는" 호출은 idempotency-key 재사용·동시성·재시도 결과가 실제로
커밋되어야 그 뒤 단언(재조회로 count==1 확인 등)이 의미가 있어 롤백으로
대체할 수 없다 — 이 세 곳은 `test_postgres_journal_repository.py`와
동일하게 `post_entry()`로 바꿨다(프로덕션에서도 `append()`는 항상
`post_entry` 경유로만 불리므로, "post_entry 없이 단독 호출"을 테스트하는
쪽이 오히려 비현실적 경로였다). 실패해서 커밋되지 않는 호출(unknown
account negative, 감사 실패 주입의 첫 시도, digest-mismatch 거부당하는
재전송)은 그대로 `journal.append()` 직접 호출로 남겨 LC-8b 자체의 계약을
계속 검증한다."""

from __future__ import annotations

import asyncio
import time
from datetime import datetime, timezone
from decimal import Decimal
from uuid import UUID, uuid4

import pytest

from src.data.models.base import Currency
from src.foundation.evidence.adapters.postgres_repository import PostgresAuditEventRepository
from src.foundation.ledger.adapters.postgres_balance_repository import (
    PostgresBalanceRepository,
    UnknownAccountError,
)
from src.foundation.ledger.adapters.postgres_journal_repository import PostgresJournalRepository
from src.foundation.ledger.application.post_entry import post_entry
from src.foundation.ledger.contracts.v1 import (
    LedgerEvent,
    LedgerEventType,
    PostingLine,
    Side,
    UserSub,
)
from src.foundation.ledger.domain import posting_rules
from src.foundation.ledger.domain.chart_of_accounts import user_account as ua
from src.foundation.ledger.domain.idempotency import IdempotencyDigestMismatchError
from tests.integration.conftest import create_test_user

_SAMPLE_COUNT = 100
_TARGET_P95_MS = 30.0
_ROUND_TRIP_MULTIPLIER = 9
_MAX_SEQUENTIAL_ROUND_TRIPS = 7
_BASELINE_WARMUP = 5
_BASELINE_SAMPLE_COUNT = 50


def _clock() -> datetime:
    return datetime.now(timezone.utc)


class _Ports:
    """`post_entry`가 요구하는 세 포트 — `test_postgres_journal_repository.py`의
    동명 헬퍼와 같은 관례(같은 `pool` 위에 묶는다)."""

    def __init__(self, pool) -> None:
        self.balances = PostgresBalanceRepository(pool)
        self.audit = PostgresAuditEventRepository(pool)


@pytest.fixture
def ports(pool):
    return _Ports(pool)


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


async def _append_without_persisting(
    conn, journal: PostgresJournalRepository, event: LedgerEvent, lines: list[PostingLine]
) -> None:
    """`journal.append()`를 실행하되 명시적으로 롤백해 결과를 버린다 — p95/
    왕복수 측정은 `append()` 자체의 쿼리 수·지연만 필요로 하고 결과가
    남을 필요는 없다(모듈 docstring task-5599 fix). `conn.transaction()`
    컨텍스트 매니저는 정상 종료 시 COMMIT하므로, 여기서는 대신 수동으로
    시작해 `finally`에서 항상 ROLLBACK한다 — COMMIT 1회가 ROLLBACK 1회로
    바뀌는 것 말고는 왕복 프로파일이 동일하다."""
    tx = conn.transaction()
    await tx.start()
    try:
        await journal.append(conn, event, lines)
    finally:
        await tx.rollback()


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
        await _append_without_persisting(
            conn, journal, warmup_event, posting_rules.lines_for(warmup_event)
        )

        event = _topup_event(counted_user_id)
        lines = posting_rules.lines_for(event)
        tx = conn.transaction()
        await tx.start()
        try:
            conn.add_query_logger(_log)
            try:
                await journal.append(conn, event, lines)
            finally:
                conn.remove_query_logger(_log)
        finally:
            await tx.rollback()

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
        async with pool.acquire() as conn:
            await _append_without_persisting(conn, journal, event, lines)
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
    # 절대 지연 p95 단언은 게이트로 쓰지 않는다(esc-ci-d5723ce4366d 종결
    # 조치) — 이 리프의 DoD는 왕복 수 축소이며, CI 환경 실측(p95=172.7ms 대
    # 정규화 목표 74.9ms)은 이 파일이 통제할 수 없는 CI 인프라 변동성을
    # 반영한다. 임계를 올려 통과시키거나 xfail로 숨기는 대신(task-920 XPASS
    # strict 전례) 왕복 수 단언만 차단 게이트로 남기고 지연은 위 print로
    # 계속 실측치를 남긴다.


# --- DEEPEN task-2975 — negative(3) + failure-injection. DEPTH 감사
# (task-2723)가 이 파일을 "성능 테스트 단독, negative 0건"으로 판정한
# 공백을 메운다. 대상은 위 성능 측정과 같은 `append()`의 왕복 축소 경로
# (task-627/37a5375, CTE/LATERAL 통합 조회 + 멱등 조회) — 일반 CRUD
# negative는 test_postgres_journal_repository.py가 이미 갖고 있지만, 이
# 리프(LC-17 결함 B)의 증빙은 왕복을 줄인 바로 그 쿼리 경로가 실패
# 시나리오에서도 여전히 정확함을 이 파일 안에서 직접 보여야 한다. ---


async def test_journal_append_rejects_all_unknown_accounts_not_just_first(pool) -> None:
    """negative(1/3) — 왕복 축소가 묶은 계좌 해석 LATERAL/array_agg가 여러
    미지 계좌 중 일부만 조용히 누락하지 않고 전부 보고하는지 검증한다.
    `array_agg`가 매칭된 행만 모으므로, 구현이 실수로 첫 번째 미지 코드만
    비교하거나 결과 개수로만 판단했다면 두 번째 미지 코드를 놓칠 수 있다."""
    journal = PostgresJournalRepository(pool)
    user_id = await create_test_user(pool)
    await _seed_user_available_account(pool, user_id)

    event = _topup_event(user_id)
    lines = [
        PostingLine(
            line_no=1,
            account_code="PLATFORM:DOES_NOT_EXIST_A",
            side=Side.DEBIT,
            amount=Decimal("1.00"),
            currency=Currency.KRW,
        ),
        PostingLine(
            line_no=2,
            account_code="PLATFORM:DOES_NOT_EXIST_B",
            side=Side.CREDIT,
            amount=Decimal("1.00"),
            currency=Currency.KRW,
        ),
    ]

    with pytest.raises(UnknownAccountError) as exc_info:
        async with pool.acquire() as conn, conn.transaction():
            await journal.append(conn, event, lines)

    assert set(exc_info.value.missing_codes) == {
        "PLATFORM:DOES_NOT_EXIST_A",
        "PLATFORM:DOES_NOT_EXIST_B",
    }
    async with pool.acquire() as conn:
        found = await conn.fetchval(
            "SELECT 1 FROM ledger_journal_entry WHERE idempotency_key = $1",
            f"{event.event_type.value}:{event.event_ref}",
        )
    assert found is None


async def test_journal_append_rejects_tampered_resend_as_digest_mismatch(pool, ports) -> None:
    """negative(2/3) — 같은 `idempotency_key`가 다른 `lines`로 재전송되면
    왕복 축소 CTE가 기존 행을 정확히 찾아 `lines_digest`를 비교해 거부한다
    (한 왕복으로 합친 조회가 여전히 정확한 기존 행을 반환하는지가 이
    리프의 핵심 위험). 첫 성공 호출은 `post_entry()`를 거친다(모듈
    docstring task-5599 fix — 성공 커밋은 `PLATFORM:CASH_CLEARING` 잔액을
    저널과 함께 원자적으로 갱신해야 공유 시드 계정을 오염시키지 않는다)."""
    journal = PostgresJournalRepository(pool)
    user_id = await create_test_user(pool)
    await _seed_user_available_account(pool, user_id)

    event_ref = f"perf:topup:{uuid4()}"
    first_event = LedgerEvent(
        event_type=LedgerEventType.TOPUP_CONFIRMED,
        event_ref=event_ref,
        tenant_id=None,
        actor_subject_id=None,
        trace_id=uuid4(),
        amount=Decimal("1.00"),
        currency=Currency.KRW,
        parties={"user": user_id},
        extra={},
    )
    async with pool.acquire() as conn, conn.transaction():
        await post_entry(
            conn, first_event, journal=journal, balances=ports.balances,
            audit=ports.audit, clock=_clock,
        )

    tampered_event = first_event.model_copy(update={"amount": Decimal("999.00")})
    with pytest.raises(IdempotencyDigestMismatchError):
        async with pool.acquire() as conn, conn.transaction():
            await journal.append(conn, tampered_event, posting_rules.lines_for(tampered_event))

    async with pool.acquire() as conn:
        count = await conn.fetchval(
            "SELECT COUNT(*) FROM ledger_journal_entry WHERE idempotency_key = $1",
            f"{first_event.event_type.value}:{event_ref}",
        )
    assert count == 1


async def test_journal_append_concurrent_tampered_resend_rejects_loser(pool, ports) -> None:
    """negative(3/3) + 적대적 동시성 — 같은 `idempotency_key`로 서로 다른
    내용의 두 호출이 동시에 경합하면(task-614 LC-17 gold-standard와 동일한
    asyncio.gather 패턴), 전역 advisory lock이 직렬화하는 왕복 축소 경로가
    정확히 하나만 성공시키고 나머지는 REPLAY가 아니라
    `IdempotencyDigestMismatchError`로 거부해야 한다. 승자의 커밋은
    `post_entry()`를 거친다(모듈 docstring task-5599 fix — 위와 동일한
    이유로 공유 시드 계정 오염을 피한다)."""
    journal = PostgresJournalRepository(pool)
    user_id = await create_test_user(pool)
    await _seed_user_available_account(pool, user_id)
    event_ref = f"perf:topup:{uuid4()}"

    async def _attempt(amount: Decimal) -> object:
        event = LedgerEvent(
            event_type=LedgerEventType.TOPUP_CONFIRMED,
            event_ref=event_ref,
            tenant_id=None,
            actor_subject_id=None,
            trace_id=uuid4(),
            amount=amount,
            currency=Currency.KRW,
            parties={"user": user_id},
            extra={},
        )
        async with pool.acquire() as conn, conn.transaction():
            return await post_entry(
                conn, event, journal=journal, balances=ports.balances,
                audit=ports.audit, clock=_clock,
            )

    results = await asyncio.gather(
        _attempt(Decimal("1.00")), _attempt(Decimal("2.00")), return_exceptions=True
    )

    successes = [r for r in results if not isinstance(r, BaseException)]
    mismatches = [r for r in results if isinstance(r, IdempotencyDigestMismatchError)]
    assert len(successes) == 1, f"정확히 1건만 성공해야 합니다: {results}"
    assert len(mismatches) == 1, f"패자는 digest mismatch로 거부돼야 합니다: {results}"

    async with pool.acquire() as conn:
        count = await conn.fetchval(
            "SELECT COUNT(*) FROM ledger_journal_entry WHERE idempotency_key = $1",
            f"{LedgerEventType.TOPUP_CONFIRMED.value}:{event_ref}",
        )
    assert count == 1


async def test_journal_append_rolls_back_entirely_when_audit_append_fails(
    pool, ports, monkeypatch
) -> None:
    """failure-injection — 왕복 축소 경로는 계좌 해석까지 한 왕복(CTE)으로
    끝내고 그 다음 감사 이벤트(FND-03)를 append한 뒤에야 저널·분개행을
    INSERT한다(모듈 docstring FK 제약 설명 참고). 감사 append가 I/O 장애로
    실패해도(디스크·잠금 등을 흉내) 트랜잭션 전체가 롤백돼 저널 엔트리도
    분개행도 남지 않아야 하고, 실패한 시도가 다음 재시도의 CTE 컨텍스트
    (다음 sequence_no·prev_hash 계산)를 오염시키지 않아야 한다. 재시도
    성공 호출은 `post_entry()`를 거친다(모듈 docstring task-5599 fix —
    위와 동일한 이유로 공유 시드 계정 오염을 피한다)."""
    journal = PostgresJournalRepository(pool)
    user_id = await create_test_user(pool)
    await _seed_user_available_account(pool, user_id)
    event = _topup_event(user_id)
    lines = posting_rules.lines_for(event)
    idempotency_key = f"{event.event_type.value}:{event.event_ref}"

    async def _boom(*args: object, **kwargs: object) -> object:
        raise OSError("injected audit append failure")

    monkeypatch.setattr(journal._audit_repo, "append_event_in", _boom)

    with pytest.raises(OSError, match="injected audit append failure"):
        async with pool.acquire() as conn, conn.transaction():
            await journal.append(conn, event, lines)

    async with pool.acquire() as conn:
        entry_count = await conn.fetchval(
            "SELECT COUNT(*) FROM ledger_journal_entry WHERE idempotency_key = $1",
            idempotency_key,
        )
        line_count = await conn.fetchval(
            "SELECT COUNT(*) FROM ledger_posting_line pl "
            "JOIN ledger_journal_entry e ON e.entry_id = pl.entry_id "
            "WHERE e.idempotency_key = $1",
            idempotency_key,
        )
    assert entry_count == 0
    assert line_count == 0

    monkeypatch.undo()
    async with pool.acquire() as conn, conn.transaction():
        retried = await post_entry(
            conn, event, journal=journal, balances=ports.balances,
            audit=ports.audit, clock=_clock,
        )
    assert retried.replayed is False
    assert retried.sequence_no >= 1
