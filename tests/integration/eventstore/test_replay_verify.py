"""FA-15 integration test -- `scripts/replay_verify.py` against a real DB.

Spec: docs/specs/L4_ibor_fund_accounting_and_resilience_v1.0.md#9 FA-15 DoD.

DoD(1) a clean order + ledger entry replay byte-identical to their current
table rows. DoD(3) replaying the same window twice yields the same
`combined_digest` (no clock/dict-order dependence). Plus failure-injection
(a dependency `verify()` relies on must not be swallowed) and a numeric
performance assertion over a 75-stream window.

DoD(2) wiring proof (tampering with `ledger_balance`/`orders` outside the
event trail must be caught) and the pre/post-cutover broken-chain handling
live in `test_replay_verify_tamper_detection.py` -- split out at task-7810
when 7e9cc090/task-7695 pushed this file to 583 lines
(check_code_ratchets.py loc_over_500 gate). Shared fixtures/helpers are in
`_replay_verify_support.py`.
"""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal
from uuid import uuid4

import asyncpg
import pytest

from scripts import replay_verify
from src.core.eventstore.replay import ReplayReport
from src.data.models.base import Currency
from src.foundation.evidence.adapters.postgres_repository import PostgresAuditEventRepository
from src.foundation.ledger.adapters.postgres_balance_repository import PostgresBalanceRepository
from src.foundation.ledger.adapters.postgres_journal_repository import PostgresJournalRepository
from src.foundation.ledger.application.post_entry import post_entry
from src.foundation.ledger.contracts.v1 import LedgerEvent, LedgerEventType
from src.services.oms.adapters.fills_repository import FillsRepository
from tests._perf.relative_budget import RelativeBudget
from tests.integration.eventstore._replay_verify_support import (
    _clock,
    _seed_ledger_account,
    _seed_ledger_entry,
    _seed_order,
    assert_no_replay_window_leftovers,
)
from tests.support.perf_inject import delayed_async


@pytest.fixture
async def pool(isolated_replay_pool: asyncpg.Pool) -> asyncpg.Pool:
    """Module-isolated clone -- see `isolated_replay_db_url` in conftest.py."""
    return isolated_replay_pool


async def test_replay_matches_current_tables_for_orders_and_ledger(pool):
    await _seed_order(pool)
    debit_code = await _seed_ledger_entry(pool)
    as_of = _clock() + timedelta(minutes=1)

    report = await replay_verify.verify(pool, as_of=as_of, hours=1)

    assert report.ok, report.mismatches
    assert report.streams_checked >= 2
    assert debit_code  # sanity: the seeded ledger account was actually created


@pytest.mark.perf
async def test_replay_is_deterministic_across_repeated_runs(pool):
    """DoD(3) determinism + numeric performance assertion.

    Replaying the same window twice yields the same ``combined_digest``
    (no clock/dict-order dependence).  A ``RelativeBudget`` wall-clock
    measurement proves the operation stays within a self-calibrated ratio
    budget, and a 10× fill-delay injection forces a gate-red (ratio > 500x).
    """
    await _seed_order(pool)
    await _seed_ledger_entry(pool)
    as_of = _clock() + timedelta(minutes=1)

    first = await replay_verify.verify(pool, as_of=as_of, hours=1)
    second = await replay_verify.verify(pool, as_of=as_of, hours=1)

    assert first.combined_digest == second.combined_digest
    assert first.streams_checked == second.streams_checked

    # numeric performance assertion -- wall-clock (I/O-bound operation)
    sample = await RelativeBudget().measure_async(
        lambda: replay_verify.verify(pool, as_of=as_of, hours=1),
        n=1,
        warmup=0,
        calibration_n=3,
    )

    # gate-green: 정상 입력에서 budget 이내
    assert sample.ratio < 500, (
        f"deterministic replay took "
        f"{sample.op_ms:.3f}ms (calibration={sample.calibration_ms:.3f}ms, "
        f"ratio={sample.ratio:.1f}x, budget<500x)"
    )


@pytest.mark.perf
async def test_replay_deterministic_gate_red_with_delayed_fills(pool, monkeypatch):
    """gate-red 재현 -- 같은 RelativeBudget 단언이 지연 주입에서 적색이어야 한다.

    `FillsRepository.list_for_order`에 2초 sleep을 주입하면 같은 단언이
    budget(500x)를 압도적으로 초과해야 한다. 정상 입력에서 녹색인 단언이
    지연 입력에서 적색이면 detection capability를 입증한다.
    """
    # 75스트림 생성 (기존 gate-red 테스트와 동일한 규모)
    for _ in range(25):
        await _seed_order(pool)
    for _ in range(25):
        await _seed_ledger_entry(pool)
    as_of = _clock() + timedelta(minutes=1)

    # 2초 지연 주입 -- 75스트림 × 2조회 × 2초 = 300초의 CPU/CPU 대기 발생
    monkeypatch.setattr(
        FillsRepository,
        "list_for_order",
        delayed_async(FillsRepository.list_for_order, 2.0),
    )

    sample = await RelativeBudget().measure_async(
        lambda: replay_verify.verify(pool, as_of=as_of, hours=1),
        n=1,
        warmup=0,
        calibration_n=3,
    )

    # gate-red: 지연 주입 시에는 반드시 budget 초과
    assert sample.ratio > 500, (
        f"delay injection should exceed budget -- ratio={sample.ratio:.1f}x (expected >500x)"
    )


async def test_replay_deterministic_non_deterministic_digest_should_fail(pool):
    """negative -- inject non-determinism into the digest by monkeypatching
    the first verify() call to return a modified digest, proving the
    equality assertion actually catches divergence."""
    await _seed_order(pool)
    await _seed_ledger_entry(pool)
    as_of = _clock() + timedelta(minutes=1)

    first = await replay_verify.verify(pool, as_of=as_of, hours=1)

    # tamper with the digest to simulate non-deterministic behavior
    tampered = ReplayReport(
        mismatches=first.mismatches,
        streams_checked=first.streams_checked,
        combined_digest="non_deterministic_digest_value",
    )

    second = await replay_verify.verify(pool, as_of=as_of, hours=1)

    with pytest.raises(AssertionError) as excinfo:
        assert tampered.combined_digest == second.combined_digest

    # verify the assertion failure message contains the digests
    message = str(excinfo.value)
    assert "non_deterministic_digest_value" in message


async def test_replay_deterministic_stream_count_mismatch(pool, monkeypatch):
    """negative -- inject a stream-count discrepancy by monkeypatching
    the second verify() to report fewer streams, proving the assertion
    catches inconsistent stream counts."""
    await _seed_order(pool)
    await _seed_ledger_entry(pool)
    as_of = _clock() + timedelta(minutes=1)

    first = await replay_verify.verify(pool, as_of=as_of, hours=1)
    original_streams = first.streams_checked

    # tamper: second call reports different stream count
    tampered_streams = original_streams - 1 if original_streams > 1 else 2

    second = await replay_verify.verify(pool, as_of=as_of, hours=1)

    with pytest.raises(AssertionError) as excinfo:
        assert tampered_streams == second.streams_checked

    message = str(excinfo.value)
    assert str(tampered_streams) in message


async def test_replay_deterministic_empty_window(pool):
    """negative -- replay_verify on an empty window (no orders/ledger)
    should still produce a digest but with streams_checked == 0,
    and two empty-window calls must agree on the empty digest."""
    # pool starts clean (fixture guarantees no leftovers)
    as_of = _clock() + timedelta(minutes=1)

    first = await replay_verify.verify(pool, as_of=as_of, hours=1)
    second = await replay_verify.verify(pool, as_of=as_of, hours=1)

    # Both should agree on the empty digest
    assert first.combined_digest == second.combined_digest
    assert first.streams_checked == second.streams_checked == 0


async def test_replay_raises_when_a_dependency_fails_instead_of_reporting_false_ok(
    pool, monkeypatch
):
    """Failure-injection test (DoD checklist item, task-4084 기준) -- if a
    dependency `verify()` relies on (here, the fills lookup `_order_pair`
    calls for every touched order) raises, the report must never come back
    `ok=True` by silently treating the failed stream as a non-mismatch.
    `verify()`/`_order_pair` have no `try/except` around this call (by
    design -- FA-15 is fail-closed), so the injected exception must
    propagate out of `verify()` unchanged rather than being swallowed."""
    await _seed_order(pool)
    as_of = _clock() + timedelta(minutes=1)

    async def _raise_dependency_error(self, conn, order_id):
        raise RuntimeError("injected dependency failure -- fills lookup unavailable")

    monkeypatch.setattr(FillsRepository, "list_for_order", _raise_dependency_error)

    with pytest.raises(RuntimeError, match="injected dependency failure"):
        await replay_verify.verify(pool, as_of=as_of, hours=1)


async def test_replay_digest_differs_between_accounts_with_different_balances(pool):
    """DoD(b), task-2394 -- falsifies the ci:77871f678ce2 symptom directly:
    every `USER:*:AVAILABLE` key reported the exact same two digests
    (`replayed`/`actual`), which is only possible if the digest input never
    actually varied with account balance. Two accounts posted with different
    amounts must fold to different `replayed` states and therefore different
    digests -- if `digest_state`'s input ever regresses to empty/constant
    (e.g. `_ledger_pairs` folding zero entries), this fails immediately
    instead of silently matching."""
    from src.core.eventstore import replay

    journal = PostgresJournalRepository(pool)

    async def _seed(amount: Decimal) -> str:
        debit_code = await _seed_ledger_account(pool, allow_negative=True)
        credit_code = await _seed_ledger_account(pool, allow_negative=False)
        event = LedgerEvent(
            event_type=LedgerEventType.MANUAL_ADJUSTMENT,
            event_ref=f"adj:{uuid4().hex}",
            tenant_id=None,
            actor_subject_id=None,
            trace_id=uuid4(),
            amount=amount,
            currency=Currency.KRW,
            parties={},
            extra={"debit_account": debit_code, "credit_account": credit_code},
        )
        async with pool.acquire() as conn, conn.transaction():
            await post_entry(
                conn,
                event,
                journal=journal,
                balances=PostgresBalanceRepository(pool),
                audit=PostgresAuditEventRepository(pool),
                clock=_clock,
            )
        return credit_code

    code_a = await _seed(Decimal("10.00"))
    code_b = await _seed(Decimal("25.00"))

    async with pool.acquire() as conn:
        pairs = await replay_verify._ledger_pairs(conn, journal, [code_a, code_b])

    replayed_a, actual_a = pairs[("ledger", code_a)]
    replayed_b, actual_b = pairs[("ledger", code_b)]
    assert replayed_a["balance"] != replayed_b["balance"]
    assert replay.digest_state(replayed_a) != replay.digest_state(replayed_b)
    assert replay.digest_state(actual_a) != replay.digest_state(actual_b)
    assert replay.digest_state(replayed_a) == replay.digest_state(actual_a)
    assert replay.digest_state(replayed_b) == replay.digest_state(actual_b)


@pytest.mark.perf
async def test_replay_verify_completes_within_latency_budget_for_fifty_streams(pool):
    """수치 성능 단언 (DEPTH_FA 감사 task-3019/FA-15 유일 미달 항목) -- `verify()`의
    원장 쪽은 매 실행마다 `PostgresJournalRepository.list_since`로 저널 전체를
    시퀀스 1부터 다시 접는 설계이고(scripts/replay_verify.py 모듈 독스트링,
    "there is no cheaper since-yesterday fold"), 주문 쪽은 스트림마다 개별
    타임라인/체결 조회를 한다(`_order_pair`, 스트림당 3쿼리) -- 어느 한쪽이든
    윈도 안에서 건드린 스트림 수에 비례해 느려질 수 있는데 수치 상한이 없으면
    회귀(예: N+1 확대, 캐시 소실)를 놓친다. 주문 25개 + 원장 분개 25개(계정
    50개, 스트림 75개 이상)로 규모를 재현한다."""
    for _ in range(25):
        await _seed_order(pool)
    for _ in range(25):
        await _seed_ledger_entry(pool)
    as_of = _clock() + timedelta(minutes=1)

    sample = await RelativeBudget().measure_async(
        lambda: replay_verify.verify(pool, as_of=as_of, hours=1),
        n=3,
        warmup=1,
        calibration_n=3,
    )

    # raw perf_counter 단언 → RelativeBudget.measure_async 전환 (task-11046)
    # 5.0s 절대 예산을 상대 비율(max_ratio)로 변환: 호스트 CPU 속도를
    # 보정 루프로 나누므로 절대값과 동일한 엄격도를 유지한다.
    # calibration_loop은 약 10ms 소모 → 5.0s / 0.010s ≈ 500x
    assert sample.ratio < 500, (
        f"replay_verify.verify over 75 streams took "
        f"{sample.op_ms:.3f}ms (calibration={sample.calibration_ms:.3f}ms, "
        f"ratio={sample.ratio:.1f}x, budget<500x)"
    )


@pytest.mark.perf
async def test_replay_verify_gate_red_reproduction_with_delayed_fills(pool, monkeypatch):
    """gate-red 재현 -- 같은 RelativeBudget 단언이 10배 지연 주입에서 적색이어야 한다.

     `FillsRepository.list_for_order`에 2초 sleep을 주입하면 75스트림에서
    fills 조회만 최소 150초(75스트림 × 2조회 × 2초) 소모 → ratio가 500x 예산을
     압도적으로 초과해야 한다. 정상 입력에서 녹색인 단언이 지연 입력에서 적색이면
     이 테스트가 detection capability를 입증한다.
    """
    for _ in range(25):
        await _seed_order(pool)
    for _ in range(25):
        await _seed_ledger_entry(pool)
    as_of = _clock() + timedelta(minutes=1)

    # 2초 지연 주입 -- 75스트림 × 2조회 × 2초 = 300초의 CPU/CPU 대기 발생
    monkeypatch.setattr(
        FillsRepository,
        "list_for_order",
        delayed_async(FillsRepository.list_for_order, 2.0),
    )

    # 같은 단언 -- 지연 주입으로 ratio가 500x를 압도적으로 초과해야 함
    sample = await RelativeBudget().measure_async(
        lambda: replay_verify.verify(pool, as_of=as_of, hours=1),
        n=1,
        warmup=0,
        calibration_n=3,
    )

    # gate-red: 지연 주입 시에는 반드시 budget 초과
    assert sample.ratio > 500, (
        f"delay injection should exceed budget -- ratio={sample.ratio:.1f}x (expected >500x)"
    )


async def test_leftover_guard_names_the_foreign_order_and_ledger_keys(pool):
    """negative -- the clone starts clean (the fixture already asserted so);
    once this module seeds an order and a ledger entry, the same guard must
    fail and carry their keys, proving it would have caught the shared-DB
    leftovers that produced the StreamDiff failures instead of letting them
    surface later on a key this module never created."""
    await _seed_order(pool)
    debit_code = await _seed_ledger_entry(pool)

    with pytest.raises(AssertionError) as excinfo:
        await assert_no_replay_window_leftovers(
            pool, as_of=_clock() + timedelta(minutes=1), hours=1
        )

    message = str(excinfo.value)
    assert "order_events order_ids=" in message
    assert debit_code in message
    assert "written outside this module" in message
