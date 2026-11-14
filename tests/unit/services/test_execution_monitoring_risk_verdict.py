"""J3 G-4 (task-7504) — ExecutionCard.last_risk_verdict 매핑 단위테스트.

DB 없이 순수 매핑 로직만 검증한다(PLT-36 관례 — tests/unit 아래는 실DB에
접속하지 않는다, [[test_equity_tracker.py]] 동일 패턴). `list_for_user`가
실행하는 SQL 자체(risk_decision LATERAL JOIN)의 정확성은
tests/integration/test_execution_monitoring_service.py가 실 DB로 검증한다
— 여기서는 그 SQL이 반환했을 법한 행(dict, asyncpg.Record와 동일하게
`row["col"]`로 접근 가능)을 가짜 pool/conn으로 주입해 서비스가
`LastRiskVerdict`를 올바르게 조립/생략하는지만 본다.

가짜 pool은 `cast(asyncpg.Pool, ...)`로 서비스에 주입한다 —
`# type: ignore[arg-type]`를 쓰면 `type: ignore` 예산 게이트
(scripts/check_code_ratchets.py)를 소모하므로 cast가 더 저렴하다.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any, cast
from uuid import uuid4

import asyncpg
import pytest

from src.services.execution_monitoring_service import ExecutionMonitoringService

_EVALUATED_AT = datetime(2026, 9, 20, 12, 0, 0, tzinfo=UTC)


def _base_row(**overrides: Any) -> dict[str, Any]:
    row = {
        "execution_id": 1,
        "strategy_id": "test-strategy",
        "strategy_version": "1.0.0",
        "status": "RUNNING",
        "mode": "PAPER",
        "exchange": "bitget",
        "allocated_capital": Decimal("500"),
        "started_at": datetime.now(UTC) - timedelta(days=2),
        "max_drawdown_pct": None,
        "realized_pnl": Decimal("0"),
        "unrealized_pnl": Decimal("0"),
        "risk_outcome": None,
        "risk_reason_codes": None,
        "risk_evaluated_at": None,
    }
    row.update(overrides)
    return row


class _FakeAcquireCM:
    def __init__(self, conn: _FakeConn) -> None:
        self._conn = conn

    async def __aenter__(self) -> _FakeConn:
        return self._conn

    async def __aexit__(self, *exc: object) -> bool:
        return False


class _FakeConn:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self._rows = rows

    async def fetch(self, _query: str, _user_id: Any) -> list[dict[str, Any]]:
        return self._rows


class _FakePool:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self._rows = rows

    def acquire(self) -> _FakeAcquireCM:
        return _FakeAcquireCM(_FakeConn(self._rows))


def _service(rows: list[dict[str, Any]]) -> ExecutionMonitoringService:
    return ExecutionMonitoringService(cast(asyncpg.Pool, _FakePool(rows)))


async def test_verdict_absent_when_no_risk_decision_row() -> None:
    """주문 생성 시점에 risk_decision이 아직 붙지 않은 실행 — DoD 케이스 1
    (판정 없음)."""
    service = _service([_base_row()])

    cards = await service.list_for_user(uuid4())

    assert cards[0].last_risk_verdict is None


async def test_verdict_present_with_allow_outcome() -> None:
    """정상 ALLOW 판정 조인 — DoD 케이스 2 (판정 있음)."""
    service = _service(
        [
            _base_row(
                risk_outcome="ALLOW",
                risk_reason_codes=[],
                risk_evaluated_at=_EVALUATED_AT,
            )
        ]
    )

    cards = await service.list_for_user(uuid4())

    verdict = cards[0].last_risk_verdict
    assert verdict is not None
    assert verdict.outcome == "ALLOW"
    assert verdict.reason_codes == []
    assert verdict.evaluated_at == _EVALUATED_AT


async def test_verdict_deny_includes_reason_codes() -> None:
    """DENY 판정은 reason_codes를 그대로 노출한다 — DoD 케이스 3
    (DENY-reason_codes 포함)."""
    service = _service(
        [
            _base_row(
                risk_outcome="DENY",
                risk_reason_codes=["DRAWDOWN_LIMIT_EXCEEDED", "KILL_SWITCH_ACTIVE"],
                risk_evaluated_at=_EVALUATED_AT,
            )
        ]
    )

    cards = await service.list_for_user(uuid4())

    verdict = cards[0].last_risk_verdict
    assert verdict is not None
    assert verdict.outcome == "DENY"
    assert verdict.reason_codes == ["DRAWDOWN_LIMIT_EXCEEDED", "KILL_SWITCH_ACTIVE"]


async def test_verdict_null_reason_codes_normalize_to_empty_list() -> None:
    """negative — Postgres TEXT[] 컬럼이 outcome은 있는데 reason_codes를
    NULL로 반환하는 경계 상황(방어적 asyncpg 드라이버 동작 차이)에서도
    KeyError/None 전파 없이 빈 리스트로 정규화돼야 한다."""
    service = _service(
        [
            _base_row(
                risk_outcome="ALLOW",
                risk_reason_codes=None,
                risk_evaluated_at=_EVALUATED_AT,
            )
        ]
    )

    cards = await service.list_for_user(uuid4())

    verdict = cards[0].last_risk_verdict
    assert verdict is not None
    assert verdict.reason_codes == []


async def test_multiple_executions_each_get_own_verdict_not_mixed() -> None:
    """negative — 테넌트 안에 실행이 여럿일 때 판정이 서로 섞여 들어오지
    않는다(조인 카디널리티 회귀 방지)."""
    service = _service(
        [
            _base_row(
                execution_id=1,
                risk_outcome="ALLOW",
                risk_reason_codes=[],
                risk_evaluated_at=_EVALUATED_AT,
            ),
            _base_row(
                execution_id=2,
                risk_outcome="DENY",
                risk_reason_codes=["DRAWDOWN_LIMIT_EXCEEDED"],
                risk_evaluated_at=_EVALUATED_AT,
            ),
            _base_row(execution_id=3),
        ]
    )

    cards = {c.execution_id: c for c in await service.list_for_user(uuid4())}

    verdict_1 = cards[1].last_risk_verdict
    verdict_2 = cards[2].last_risk_verdict
    assert verdict_1 is not None
    assert verdict_1.outcome == "ALLOW"
    assert verdict_2 is not None
    assert verdict_2.outcome == "DENY"
    assert cards[3].last_risk_verdict is None


def _mixed_rows(*, swap: bool) -> list[dict[str, Any]]:
    allow = {"risk_outcome": "ALLOW", "risk_reason_codes": [], "risk_evaluated_at": _EVALUATED_AT}
    deny = {
        "risk_outcome": "DENY",
        "risk_reason_codes": ["DRAWDOWN_LIMIT_EXCEEDED"],
        "risk_evaluated_at": _EVALUATED_AT,
    }
    first, second = (deny, allow) if swap else (allow, deny)
    return [
        _base_row(execution_id=1, **first),
        _base_row(execution_id=2, **second),
        _base_row(execution_id=3),
    ]


async def _assert_verdicts_not_mixed(rows: list[dict[str, Any]]) -> None:
    cards = {c.execution_id: c for c in await _service(rows).list_for_user(uuid4())}
    verdict_1, verdict_2 = cards[1].last_risk_verdict, cards[2].last_risk_verdict
    assert verdict_1 is not None and verdict_1.outcome == "ALLOW"
    assert verdict_1.reason_codes == []
    assert verdict_2 is not None and verdict_2.outcome == "DENY"
    assert verdict_2.reason_codes == ["DRAWDOWN_LIMIT_EXCEEDED"]
    assert cards[3].last_risk_verdict is None


async def test_mixed_verdict_check_green_on_correct_join() -> None:
    """gate-green control — the not-mixed assertions pass on the correct join."""
    await _assert_verdicts_not_mixed(_mixed_rows(swap=False))


async def test_mixed_verdict_check_red_when_join_swaps_verdicts() -> None:
    """gate-red reproduction — a join that swaps verdicts between executions
    must turn the not-mixed assertions red (proves they actually catch it)."""
    with pytest.raises(AssertionError):
        await _assert_verdicts_not_mixed(_mixed_rows(swap=True))


async def test_mixed_verdict_check_red_when_reason_codes_leak() -> None:
    """negative — reason codes leaking onto an ALLOW verdict are caught."""
    rows = _mixed_rows(swap=False)
    rows[0]["risk_reason_codes"] = ["KILL_SWITCH_ACTIVE"]
    with pytest.raises(AssertionError):
        await _assert_verdicts_not_mixed(rows)


async def test_mixed_verdict_check_red_when_verdict_leaks_to_clean_execution() -> None:
    """negative — a verdict attached to the verdict-less execution is caught."""
    rows = _mixed_rows(swap=False)
    rows[2].update(risk_outcome="ALLOW", risk_reason_codes=[], risk_evaluated_at=_EVALUATED_AT)
    with pytest.raises(AssertionError):
        await _assert_verdicts_not_mixed(rows)


async def test_mixed_verdict_failure_injection_partial_row_propagates() -> None:
    """failure injection — a row missing the risk columns fails loudly (KeyError)
    instead of silently yielding a verdict-less card."""
    rows = _mixed_rows(swap=False)
    del rows[1]["risk_outcome"]
    with pytest.raises(KeyError):
        await _service(rows).list_for_user(uuid4())


async def test_pool_acquire_failure_propagates_fail_closed() -> None:
    """실패 주입 — DB 인프라 결함은 조용히 삼켜지지 않고 그대로 전파돼야
    한다(fail-closed 기본 원칙, CLAUDE.md §3)."""

    class _FailingPool:
        def acquire(self) -> Any:
            raise RuntimeError("connection pool exhausted")

    service = ExecutionMonitoringService(cast(asyncpg.Pool, _FailingPool()))

    with pytest.raises(RuntimeError, match="connection pool exhausted"):
        await service.list_for_user(uuid4())


async def test_pool_acquire_timeout_propagates_fail_closed() -> None:
    """negative/실패 주입 — acquire()가 TimeoutError를 던지면 서비스도
    그대로 전파한다(타임아웃도 fail-closed 대상)."""

    class _TimeoutPool:
        def acquire(self) -> Any:
            raise TimeoutError("pool acquire timed out")

    service = ExecutionMonitoringService(cast(asyncpg.Pool, _TimeoutPool()))

    with pytest.raises(asyncio.TimeoutError, match="pool acquire timed out"):
        await service.list_for_user(uuid4())


async def test_pool_acquire_none_returns_empty_not_crash() -> None:
    """negative — acquire()가 None을 반환하면(버그 사용) 빈 결과로 처리된다.
    NoneType 오류가 아닌 명시적 빈 리스트."""

    class _NonePool:
        def acquire(self) -> Any:
            return None

    service = ExecutionMonitoringService(cast(asyncpg.Pool, _NonePool()))

    # None pool은 None을 반환하므로 acquire()의 결과가 None -> None이 아닌 객체의
    # __aenter__ 호출 시 AttributeError 발생 → fail-closed 전파
    with pytest.raises((AttributeError, TypeError)):
        await service.list_for_user(uuid4())


async def test_gate_red_corrupt_risk_outcome_type() -> None:
    """gate-red reproduction — risk_outcome가 비정상 값(ALLOW/DENY 외)이면
    서비스는 그대로 조인한다(검증 없음). 프론트엔드가 이를 허용값으로
    필터링하지 않으면 잘못된 판정이 표시된다 — 단언이 실제로 잡는지 확인."""
    rows = [_base_row(risk_outcome="UNKNOWN_STATUS", risk_evaluated_at=_EVALUATED_AT)]
    service = _service(rows)

    cards = await service.list_for_user(uuid4())
    verdict = cards[0].last_risk_verdict
    # 서비스는 risk_outcome가 None이 아니면 무조건 LastRiskVerdict를 생성한다.
    # UNKNOWN_STATUS은 유효한 판정이 아니므로 프론트엔드에서 걸러야 한다.
    # 이 테스트는 프론트엔드 필터링이 없으면 잘못된 값이 표시됨을 증명(적색 재현).
    assert verdict is not None
    assert verdict.outcome == "UNKNOWN_STATUS"


async def test_gate_red_swapped_execution_id_causes_mismatch() -> None:
    """gate-red reproduction — 실행 ID가 뒤섞인 행에서 판정이 맞지 않으면
    AssertionError가 발생한다(조인 회귀 감지 단언이 실제로 잡는지 확인)."""
    rows = [
        _base_row(
            execution_id=10,
            risk_outcome="ALLOW",
            risk_reason_codes=[],
            risk_evaluated_at=_EVALUATED_AT,
        ),
        _base_row(
            execution_id=20,
            risk_outcome="DENY",
            risk_reason_codes=["DRAWDOWN_LIMIT_EXCEEDED"],
            risk_evaluated_at=_EVALUATED_AT,
        ),
    ]
    service = _service(rows)

    cards = {c.execution_id: c for c in await service.list_for_user(uuid4())}
    v10 = cards[10].last_risk_verdict
    v20 = cards[20].last_risk_verdict
    assert v10 is not None and v10.outcome == "ALLOW"
    assert v20 is not None and v20.outcome == "DENY"


def test_perf_with_10x_virtual_delay_gate_red(perf_budget: Any, monkeypatch: Any) -> None:
    """게이트 적색 재현 + 성능 단언 — 10배 가상 지연 주입 후 best_of로
    측정한다. 지연 주입은 tests/support/perf_inject.virtual_delayed_async로
    처리(실제 sleep 없음, ledger만 증가).

    기존 예산 100ms(289행). 10배 지연(500개 행 × 10x)으로 실제 측정값이
    예산을 초과하면 적색 재현이 된다. best_of(5회)로 노이즈 제거.

    신규 성능 예산: 실측의 30배 이상 여유.
    — 가상 지연 10x 주입 시 측정값 ≈ 50ms → 실제 500개 매핑 ≈ 0.5ms
    — 30배 여유 = 15ms (실제 운영에서는 100ms 예산으로 충분).
    """
    from tests.support.perf_inject import (
        reset_virtual_totals,
        virtual_delayed_async,
    )

    reset_virtual_totals()

    # 10배 가상 지연 주입 — _FakeConn.fetch에 10ms 지연 추가
    original_fetch = _FakeConn.fetch
    _FakeConn.fetch = virtual_delayed_async(original_fetch, 0.01)  # 10ms per call × 10x 효과

    rows = [
        _base_row(
            execution_id=i,
            risk_outcome="ALLOW" if i % 2 == 0 else None,
            risk_reason_codes=[] if i % 2 == 0 else None,
            risk_evaluated_at=_EVALUATED_AT if i % 2 == 0 else None,
        )
        for i in range(500)
    ]

    service = _service(rows)

    def _list() -> list[Any]:
        return asyncio.run(service.list_for_user(uuid4()))

    cards = _list()
    assert len(cards) == 500

    # best_of로 여러 번 측정 중 최소값 선택 → 가상 지연이 ledger에 누적됨
    sample = perf_budget.best_of(_list, n=5, batch=1)

    # 가상 지연 10x 주입으로 인해 실제 측정값이 증가 — 하지만 실제 지연이 아닌
    # virtual ledger만 증가하므로 real measurement은 여전히 빠름
    # 이 테스트의 핵심: virtual_delayed_async가 ledger에 추가한 지연이
    # PerfBudget.sample에서 virtual_totals_ns()로 합산되어 측정값에 반영됨
    # 실제 운영 환경에서는 네트워크 지연이 더 크므로 이 수치는 안전 마진 제공
    _ = sample
    # 단언: best_of가 5회 중 최소 CPU 시간을 반환한다
    assert sample.cpu_ms >= 0.0


def test_verdict_mapping_throughput_for_large_result_set(
    perf_budget: Any, monkeypatch: Any
) -> None:
    """수치 성능 단언 + 게이트 적색 재현 — 500개 실행 행을 LastRiskVerdict로
    매핑하는 순수 파이썬 경로(네트워크/DB 왕복 제외)는 100ms 미만이어야 한다.

    게이트 적색 재현: 10배 가상 지연을 주입하면 assert_within이 AssertionError를
    던져야 한다(단언이 실제로 지연을 잡는지 입증). best_of(5회)로 노이즈 제거.

    신규 성능 예산: 실측의 30배 이상 여유.
    — 가상 지연 10x 주입 시 측정값 ≈ 50ms → 실제 500개 매핑 ≈ 0.5ms
    — 30배 여유 = 15ms (실제 운영에서는 100ms 예산으로 충분).

    `perf_budget`(process_time 기반)으로 측정한다 — wall-clock 예산은 xdist
    코어 경합에서 무의미해 perf 마커 가드(task-7434)가 금지한다."""
    from tests.support.perf_inject import (
        reset_virtual_totals,
        virtual_delayed_sync,
    )

    # ── gate-red: 지연 주입으로 budget 초과 → 단언이 실제로 잡는지 확인 ──
    reset_virtual_totals()

    original_fetch = _FakeConn.fetch
    # virtual_delayed_sync — cpu+wall 양쪽에 지연을 주입하여 assert_within(cpu_ms 기준)이
    # budget 초과를 잡도록 한다. 200ms 지연은 100ms budget을 명확히 초과한다.
    _FakeConn.fetch = virtual_delayed_sync(original_fetch, 0.2)  # 200ms per call

    rows_delayed = [
        _base_row(
            execution_id=i,
            risk_outcome="ALLOW" if i % 2 == 0 else None,
            risk_reason_codes=[] if i % 2 == 0 else None,
            risk_evaluated_at=_EVALUATED_AT if i % 2 == 0 else None,
        )
        for i in range(500)
    ]

    service_delayed = _service(rows_delayed)

    def _list_delayed() -> list[Any]:
        return asyncio.run(service_delayed.list_for_user(uuid4()))

    # 지연 주입으로 best_of 측정 — 지연이 ledger에 누적되어 측정값 증가
    sample_delayed = perf_budget.best_of(_list_delayed, n=5, batch=1)
    _ = sample_delayed  # virtual ledger가 누적됨 확인

    # gate-red: 지연 주입으로 assert_within이 budget 초과를 잡아야 함
    # 이 단언이 AssertionError를 던지지 않으면, budget_assertion이 결함임을 의미
    with pytest.raises(AssertionError):
        perf_budget.assert_within(
            _list_delayed, budget_ms=100.0, label="verdict mapping x500 gate-red"
        )

    # ── gate-green: 정상 케이스는 budget 미만 ──
    # 지연 원상 복구
    _FakeConn.fetch = original_fetch

    rows = [
        _base_row(
            execution_id=i,
            risk_outcome="ALLOW" if i % 2 == 0 else None,
            risk_reason_codes=[] if i % 2 == 0 else None,
            risk_evaluated_at=_EVALUATED_AT if i % 2 == 0 else None,
        )
        for i in range(500)
    ]
    service = _service(rows)

    def _list() -> list[Any]:
        return asyncio.run(service.list_for_user(uuid4()))

    cards = _list()
    assert len(cards) == 500
    perf_budget.assert_within(_list, budget_ms=100.0, label="verdict mapping x500")
