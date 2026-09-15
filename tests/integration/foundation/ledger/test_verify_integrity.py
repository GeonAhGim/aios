"""LC-10 `verify_ledger_integrity` 통합테스트 — 실 DB(TEST_DATABASE_URL) 대상.

Spec: docs/specs/L4_market_data_positions_ledger_v1.0.md#§6, §9 LC-10.
DoD(task-335): "정상 리포트; 행 변조(superuser로) → 체인 실패 →
write_frozen=true → post_entry 거부"(spec 605행 test 목록과 동일 시나리오).

경로는 기존 LC-8/LC-9 통합테스트와 같은 `tests/integration/foundation/ledger/`
디렉터리를 쓴다 — task 파일의 `tests/foundation/integration/ledger/`는 이
저장소에 존재하지 않는 경로 순서이고, 실제 원장 통합테스트는 전부
`tests/integration/foundation/ledger/`에 모여 그 디렉터리의 `conftest.py`
`pool` 픽스처를 공유한다.

task-2957(DEEPEN 420, docs/audit/DEPTH_LA_LB_LC.md): D2 축 하한 미달
근거 3건을 이 리프에서 보강한다 — ① negative는 기존 체인단절(①) 1건뿐이라
2건을 더한다: 시산표만 깨지는 ②경로 전용 변조(체인은 위조해서 통과시키되
Σ≠0만 남기는 정교한 변조, 아래 `test_verify_ledger_integrity_freezes_
on_trial_balance_nonzero_and_blocks_posting`)와, task-312/b120c35c가 고친
`ledger_control` 잔류 격리 결함을 직접 재현하는 회귀 테스트(아래
`test_verify_ledger_integrity_residual_freeze_blocks_unrelated_posting_
until_cleared` — 이게 동시에 게이트 적색 재현 증빙이다). ② 수치 성능
단언은 `test_verify_ledger_integrity_completes_within_one_cycle_safety_
margin`이 §7 LC-10 SLO("5분 주기 100% 성공") 대비 넉넉한 안전마진으로
건다 — 공유 TEST_DATABASE_URL은 다른 리프가 남긴 엔트리로 계속 자라
task-920/1029(LC-17)가 겪은 것과 같은 이유로 타이트한 절대 지연 임계는
이 환경에서 못 쓴다.

변조는 `ledger_journal_entry`가 WORM(L0-3, `BEFORE UPDATE OR DELETE` 트리거)
이라 트리거를 일시적으로 `DISABLE`해야 한다 — "superuser가 트리거까지
우회해 직접 행을 바꿨다"는 시나리오를 그대로 재현한다(REVOKE만으로는 막지
못하는 이유가 정확히 이것, `src/core/db/append_only.py` 참고). `lines_digest`
컬럼 하나만 건드려 이 엔트리의 체인 검증만 깨뜨리고 `entry_hash`는
그대로 둔다 — 그래야 이후 엔트리들의 `prev_hash` 연결은 안 깨져서 실패
지점(`first_broken_seq`)이 정확히 이 엔트리로 특정된다. 손상 주입부터
`lines_digest` 원복까지를 하나의 try/finally로 감싸 빈틈없이 복원한다
(`test_post_entry.py`의 동결 테스트와 동일 관행) — `ledger_control`은
전체 스위트가 공유하는 전역 상태라, 이 테스트가 도중에 죽어 자체 복원을
못 밟는 경우까지 대비해 `conftest.py`의 `_ledger_control_clean_slate`
autouse fixture가 매 테스트 전후로 무조건 원복하는 전역 안전망 역할을
한다(task-312 QA가 재현한 "이전 실행의 write_frozen 잔류가 재실행을
깨뜨리는" 격리 결함의 근본 수정).
"""

from __future__ import annotations

import time
from datetime import datetime, timezone
from decimal import Decimal
from uuid import UUID, uuid4

import pytest

from src.core.observability.metrics_registry import MetricsRegistry
from src.data.models.base import Currency
from src.foundation.evidence.adapters.postgres_repository import PostgresAuditEventRepository
from src.foundation.ledger.adapters.postgres_balance_repository import PostgresBalanceRepository
from src.foundation.ledger.adapters.postgres_journal_repository import PostgresJournalRepository
from src.foundation.ledger.application.post_entry import LedgerWriteFrozenError, post_entry
from src.foundation.ledger.application.verify_integrity import verify_ledger_integrity
from src.foundation.ledger.contracts.v1 import (
    AccountType,
    LedgerEvent,
    LedgerEventType,
    UserSub,
)
from src.foundation.ledger.domain.chart_of_accounts import user_account
from src.foundation.ledger.domain.hash_chain import entry_hash, lines_digest

_WORM_TRIGGER = "ledger_journal_entry_worm_guard_trg"
_POSTING_LINE_WORM_TRIGGER = "ledger_posting_line_worm_guard_trg"
_MAX_VERIFY_DURATION_MS = 60_000.0


def _clock() -> datetime:
    return datetime.now(timezone.utc)


async def _create_user_available_account(pool, user_id: UUID) -> str:
    """잔액은 항상 0으로 시작한다(FA-15a/esc-2115: `ledger_balance`에 잔액을
    raw로 심지 않는다 — 이 파일의 테스트가 실제로 필요로 하는 잔액은 전부
    이후 `post_entry` 호출이 만든다)."""
    code = user_account(user_id, UserSub.AVAILABLE)
    async with pool.acquire() as conn:
        account_id = await conn.fetchval(
            "INSERT INTO ledger_account (account_code, account_type, currency, allow_negative) "
            "VALUES ($1, $2, $3, FALSE) RETURNING account_id",
            code,
            AccountType.LIABILITY.value,
            Currency.KRW.value,
        )
        await conn.execute(
            "INSERT INTO ledger_balance (account_id, allow_negative, last_entry_seq) "
            "VALUES ($1, FALSE, 0)",
            account_id,
        )
    return code


def _topup_event(
    *, event_ref: str, user_id: UUID, amount: Decimal = Decimal("10.00")
) -> LedgerEvent:
    return LedgerEvent(
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


class _Ports:
    def __init__(self, pool):
        self.journal = PostgresJournalRepository(pool)
        self.balances = PostgresBalanceRepository(pool)
        self.audit = PostgresAuditEventRepository(pool)


@pytest.fixture
def ports(pool):
    return _Ports(pool)


async def _post_topup(pool, ports, user_id: UUID, amount: Decimal = Decimal("10.00")):
    user_code = await _create_user_available_account(pool, user_id)
    event = _topup_event(event_ref=f"topup:{uuid4().hex}", user_id=user_id, amount=amount)
    async with pool.acquire() as conn, conn.transaction():
        view = await post_entry(
            conn,
            event,
            journal=ports.journal,
            balances=ports.balances,
            audit=ports.audit,
            clock=_clock,
        )
    return user_code, view


async def test_verify_ledger_integrity_reports_ok_when_chain_intact(pool, ports):
    await _post_topup(pool, ports, uuid4())

    report = await verify_ledger_integrity(
        journal=ports.journal,
        balances=ports.balances,
        audit=ports.audit,
        pool=pool,
        registry=MetricsRegistry(),
    )

    # `report.drifts`는 여기서 단언하지 않는다 — 이 디렉터리의 다른 리프
    # 테스트(`test_postgres_journal_repository.py`)가 `journal.append`만
    # 직접 호출해 `PLATFORM:CASH_CLEARING`에 영구적이고 정상적인
    # fold-vs-balance 드리프트를 남기기 때문(모듈 docstring 참고). 여기서
    # 확인할 안전 불변은 "체인·시산표가 멀쩡하면 절대 동결되지 않는다"는
    # 것뿐이다 — 드리프트만으로는 동결되지 않는다.
    assert report.chain_ok is True
    assert report.zero_sum_ok is True
    assert report.first_broken_seq is None

    async with pool.acquire() as conn:
        frozen = await conn.fetchval("SELECT write_frozen FROM ledger_control WHERE id = 1")
    assert frozen is False


async def test_verify_ledger_integrity_freezes_and_blocks_posting_on_tamper(pool, ports):
    _, view = await _post_topup(pool, ports, uuid4())
    entry_id = view.entry_id

    async with pool.acquire() as conn:
        original_digest = await conn.fetchval(
            "SELECT lines_digest FROM ledger_journal_entry WHERE entry_id = $1", entry_id
        )

    async def _set_digest(new_digest: str) -> None:
        # WORM 트리거를 우회해 값을 바꾸는 유일한 목적이 "변조 후 원상복구"라,
        # 손상 주입과 복원을 같은 헬퍼로 묶는다 — 복원도 결국 같은 종류의
        # 트리거-우회 UPDATE라 로직을 둘로 나눌 이유가 없고, 이렇게 하나로
        # 합쳐야 아래 try/finally 안에서 손상 주입 시점부터 복원까지 빈틈없이
        # 감싸진다(예전 버전은 손상 주입이 try 진입 *전*에 일어나 그 사이에
        # 예외가 나면 finally가 아예 실행되지 않는 구간이 있었다).
        async with pool.acquire() as conn:
            await conn.execute(f"ALTER TABLE ledger_journal_entry DISABLE TRIGGER {_WORM_TRIGGER}")
            try:
                await conn.execute(
                    "UPDATE ledger_journal_entry SET lines_digest = $1 WHERE entry_id = $2",
                    new_digest,
                    entry_id,
                )
            finally:
                await conn.execute(
                    f"ALTER TABLE ledger_journal_entry ENABLE TRIGGER {_WORM_TRIGGER}"
                )

    try:
        await _set_digest("tampered" * 8)

        report = await verify_ledger_integrity(
            journal=ports.journal,
            balances=ports.balances,
            audit=ports.audit,
            pool=pool,
            registry=MetricsRegistry(),
        )
        assert report.chain_ok is False
        assert report.first_broken_seq == view.sequence_no

        async with pool.acquire() as conn:
            frozen = await conn.fetchval("SELECT write_frozen FROM ledger_control WHERE id = 1")
            reason = await conn.fetchval("SELECT frozen_reason FROM ledger_control WHERE id = 1")
        assert frozen is True
        assert reason is not None and "chain_broken" in reason

        rejected_event = _topup_event(event_ref=f"topup:{uuid4().hex}", user_id=uuid4())
        with pytest.raises(LedgerWriteFrozenError):
            async with pool.acquire() as conn, conn.transaction():
                await post_entry(
                    conn,
                    rejected_event,
                    journal=ports.journal,
                    balances=ports.balances,
                    audit=ports.audit,
                    clock=_clock,
                )
    finally:
        # `ledger_control.write_frozen` 원복은 `conftest.py`의
        # `_ledger_control_clean_slate` autouse fixture teardown이 전역
        # 안전망으로 보장한다(이 테스트가 여기서 죽어도 커버) — 여기서는
        # 이 테스트가 직접 손상시킨 `lines_digest`만 원복한다.
        await _set_digest(original_digest)


async def test_verify_ledger_integrity_freezes_on_trial_balance_nonzero_and_blocks_posting(
    pool, ports
):
    """②(시산표 Σ=0) 경로 전용 검증 — 해시체인(①)은 멀쩡한데 시산표만 깨진
    경우를 재현한다. `ledger_posting_line`에는 커밋 시점 개별 분개 균형을
    강제하는 DEFERRABLE 제약 트리거(`ledger_entry_balanced_trg`, 마이그레이션
    `4a1d0c0de005`)가 있어 애초에 불균형 분개를 INSERT로 만들 수 없다 — 그
    트리거는 `AFTER INSERT`에만 붙어 있어 이미 커밋된 균형 분개를 WORM
    우회로 사후 UPDATE하면 다시 발동하지 않는다(위 tamper 테스트와 같은
    수법). 다만 amount만 바꾸고 끝내면 `verify_chain`이 `lines_digest`
    불일치로 먼저 걸려 ①에서 막히므로(그 경로는 위 tamper 테스트가 이미
    검증한다), `lines_digest`·`entry_hash`까지 변조된 내용에 맞춰 함께
    재계산해 둔다 — "체인은 위조했지만 잔고 항등식까지는 못 맞춘" 더
    정교한 변조를 재현해야 ②만 단독으로 걸리는 경로를 검증할 수 있다. 이
    엔트리가 이번 검증 시점의 마지막 엔트리여야 entry_hash를 바꿔도 그
    뒤를 잇는 엔트리의 prev_hash 연결이 끊어지지 않는다 — 이 저장소 CI는
    xdist 병렬 워커를 쓰지 않아(task-1986, pyproject.toml) 이 테스트 실행
    중 다른 프로세스가 동시에 append할 위험이 없다."""
    await _post_topup(pool, ports, uuid4())
    _, view = await _post_topup(pool, ports, uuid4())
    entry_id = view.entry_id
    target_line = view.lines[0]

    async with pool.acquire() as conn:
        tampered_line_id = await conn.fetchval(
            "SELECT line_id FROM ledger_posting_line WHERE entry_id = $1 AND line_no = $2",
            entry_id,
            target_line.line_no,
        )

    original_amount = target_line.amount
    tampered_amount = original_amount + Decimal("1.00")
    tampered_lines = [
        line.model_copy(update={"amount": tampered_amount})
        if line.line_no == target_line.line_no
        else line
        for line in view.lines
    ]
    tampered_digest = lines_digest(tampered_lines)
    tampered_hash = entry_hash(
        view.prev_hash,
        view.sequence_no,
        view.event_type,
        view.event_ref,
        tampered_digest,
        view.posted_at,
    )
    original_digest = view.lines_digest
    original_hash = view.entry_hash

    async def _apply(amount: Decimal, digest: str, entry_hash_value: str) -> None:
        async with pool.acquire() as conn:
            await conn.execute(
                f"ALTER TABLE ledger_posting_line DISABLE TRIGGER {_POSTING_LINE_WORM_TRIGGER}"
            )
            try:
                await conn.execute(
                    "UPDATE ledger_posting_line SET amount = $1 WHERE line_id = $2",
                    amount,
                    tampered_line_id,
                )
            finally:
                await conn.execute(
                    f"ALTER TABLE ledger_posting_line ENABLE TRIGGER {_POSTING_LINE_WORM_TRIGGER}"
                )

            await conn.execute(f"ALTER TABLE ledger_journal_entry DISABLE TRIGGER {_WORM_TRIGGER}")
            try:
                await conn.execute(
                    "UPDATE ledger_journal_entry SET lines_digest = $1, entry_hash = $2 "
                    "WHERE entry_id = $3",
                    digest,
                    entry_hash_value,
                    entry_id,
                )
            finally:
                await conn.execute(
                    f"ALTER TABLE ledger_journal_entry ENABLE TRIGGER {_WORM_TRIGGER}"
                )

    try:
        await _apply(tampered_amount, tampered_digest, tampered_hash)

        report = await verify_ledger_integrity(
            journal=ports.journal,
            balances=ports.balances,
            audit=ports.audit,
            pool=pool,
            registry=MetricsRegistry(),
        )
        assert report.chain_ok is True
        assert report.first_broken_seq is None
        assert report.zero_sum_ok is False

        async with pool.acquire() as conn:
            frozen = await conn.fetchval("SELECT write_frozen FROM ledger_control WHERE id = 1")
            reason = await conn.fetchval("SELECT frozen_reason FROM ledger_control WHERE id = 1")
        assert frozen is True
        assert reason is not None and "trial_balance_nonzero" in reason

        rejected_event = _topup_event(event_ref=f"topup:{uuid4().hex}", user_id=uuid4())
        with pytest.raises(LedgerWriteFrozenError):
            async with pool.acquire() as conn, conn.transaction():
                await post_entry(
                    conn,
                    rejected_event,
                    journal=ports.journal,
                    balances=ports.balances,
                    audit=ports.audit,
                    clock=_clock,
                )
    finally:
        await _apply(original_amount, original_digest, original_hash)


async def test_verify_ledger_integrity_residual_freeze_blocks_unrelated_posting_until_cleared(
    pool, ports
):
    """게이트 적색 재현: task-312 QA가 실측하고 b120c35c가 고친 결함을 직접
    재현한다 — 원장 자체는 완전히 멀쩡해도 `ledger_control.write_frozen`
    잔류(예: 이전 실행이 tamper 테스트 도중 죽어 자체 finally를 못 밟은
    경우)만으로 이후 모든 `post_entry` 호출이 영구히 거부된다. 이
    디렉터리의 `conftest.py`가 매 테스트 전후로 무조건 원복하는
    `_ledger_control_clean_slate` autouse fixture가 정확히 이 결함의 실제
    수정이다 — 이 테스트는 그 자기치유가 없었다면 무슨 일이 벌어졌는지
    (잔류가 무관한 포스팅까지 거부)를 직접 재현하고, 수동 정리 후에는
    원장이 실제로는 멀쩡했으므로 즉시 회복됨을 확인한다."""
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE ledger_control SET write_frozen = TRUE, "
            "frozen_reason = 'simulated residue from a crashed prior test run (task-312)', "
            "frozen_at = now() WHERE id = 1"
        )

    try:
        leaked_event = _topup_event(event_ref=f"topup:{uuid4().hex}", user_id=uuid4())
        with pytest.raises(LedgerWriteFrozenError):
            async with pool.acquire() as conn, conn.transaction():
                await post_entry(
                    conn,
                    leaked_event,
                    journal=ports.journal,
                    balances=ports.balances,
                    audit=ports.audit,
                    clock=_clock,
                )

        report = await verify_ledger_integrity(
            journal=ports.journal,
            balances=ports.balances,
            audit=ports.audit,
            pool=pool,
            registry=MetricsRegistry(),
        )
        assert report.chain_ok is True
        assert report.zero_sum_ok is True
    finally:
        async with pool.acquire() as conn:
            await conn.execute(
                "UPDATE ledger_control SET write_frozen = FALSE, frozen_reason = NULL, "
                "frozen_at = NULL WHERE id = 1"
            )

    _, recovered_view = await _post_topup(pool, ports, uuid4())
    assert recovered_view.replayed is False


@pytest.mark.perf
async def test_verify_ledger_integrity_completes_within_one_cycle_safety_margin(pool, ports):
    """§7 LC-10 SLO는 "5분 주기 100% 성공"이다 — 다음 주기가 겹치지 않으려면
    한 번의 실행이 그 주기보다 훨씬 짧게 끝나야 한다. 이 공유
    TEST_DATABASE_URL은 다른 리프가 남긴 엔트리로 계속 자라(task-920/1029가
    LC-17 `journal.append` perf 테스트에서 겪은 것과 같은 이유로) 절대
    지연에 타이트한 임계를 걸면 이 환경에서 신뢰할 수 없다 — 그래서
    여기서는 5분 주기의 20%(60초)라는 넉넉한 안전마진만 회귀 게이트로
    걸고, 실측치는 관측성을 위해 print로 남긴다."""
    await _post_topup(pool, ports, uuid4())

    started = time.perf_counter()
    report = await verify_ledger_integrity(
        journal=ports.journal,
        balances=ports.balances,
        audit=ports.audit,
        pool=pool,
        registry=MetricsRegistry(),
    )
    elapsed_ms = (time.perf_counter() - started) * 1000

    print(
        f"\nledger verify_ledger_integrity latency: {elapsed_ms:.3f}ms "
        f"(entries_verified={report.entries_verified}, ceiling={_MAX_VERIFY_DURATION_MS}ms)"
    )

    assert elapsed_ms < _MAX_VERIFY_DURATION_MS, (
        f"verify_ledger_integrity가 {elapsed_ms:.1f}ms 걸렸습니다 — §7 5분 주기 "
        f"안전마진({_MAX_VERIFY_DURATION_MS}ms)을 초과해 다음 주기와 겹칠 위험이 있습니다."
    )
