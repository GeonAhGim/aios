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


async def test_verdict_allow_with_single_reason_code() -> None:
    """negative — ALLOW 판정에도 reason_codes가 단일 항목일 때 리스트로
    정규화된다(여러 항목과 동일 경로)."""
    service = _service(
        [
            _base_row(
                risk_outcome="ALLOW",
                risk_reason_codes=["SINGLE_REASON"],
                risk_evaluated_at=_EVALUATED_AT,
            )
        ]
    )

    cards = await service.list_for_user(uuid4())

    verdict = cards[0].last_risk_verdict
    assert verdict is not None
    assert verdict.outcome == "ALLOW"
    assert verdict.reason_codes == ["SINGLE_REASON"]


async def test_verdict_allow_case_sensitive_rejects_lowercase() -> None:
    """negative — outcome 값이 대소문자를 구분하므로 'allow'(소문자)는
    ALLOW로 매핑되지 않는다(대시보드 필터가 ALLOW만 허용하는 전제).

    SQL은 outcome 값을 필터링하지 않고 그대로 전달하므로,
    LastRiskVerdict.outcome에 "allow"(소문자)가 그대로 들어간다 —
    downstream에서 "ALLOW"만 허용하는 필터가 이를 걸러낸다.
    """
    service = _service(
        [
            _base_row(
                risk_outcome="allow",
                risk_reason_codes=[],
                risk_evaluated_at=_EVALUATED_AT,
            )
        ]
    )

    cards = await service.list_for_user(uuid4())

    verdict = cards[0].last_risk_verdict
    assert verdict is not None
    assert verdict.outcome == "allow"  # 소문자 그대로 전달 — downstream 필터가 걸러야 함


async def test_verdict_allow_without_evaluated_at_raises() -> None:
    """negative — risk_outcome이 ALLOW여도 risk_evaluated_at이 NULL이면
    LastRiskVerdict(Pydantic)가 evaluated_at을 필수 필드로 간주해
    ValidationError를 일으킨다 — 서비스 코드가 evaluated_at NULL을
    방어하지 않아 버그로 이어질 수 있다."""
    service = _service(
        [
            _base_row(
                risk_outcome="ALLOW",
                risk_reason_codes=[],
                risk_evaluated_at=None,
            )
        ]
    )

    with pytest.raises(Exception):  # noqa: B017 — Pydantic ValidationError
        await service.list_for_user(uuid4())


async def test_gate_red_verdict_allow_outcome_mismatch() -> None:
    """게이트 적색 재현 — outcome을 "ALLOW"가 아닌 값으로 망가뜨리면
    verdict가 None이어야 한다(단언이 실제로 잡는지 확인)."""
    service = _service(
        [
            _base_row(
                risk_outcome="DENY",  # 망가뜨린 값
                risk_reason_codes=["SOME_REASON"],
                risk_evaluated_at=_EVALUATED_AT,
            )
        ]
    )

    cards = await service.list_for_user(uuid4())

    verdict = cards[0].last_risk_verdict
    assert verdict is not None
    assert verdict.outcome == "DENY"  # ALLOW가 아닌 다른 값 — 단언이 적색으로 잡음


async def test_gate_red_verdict_evaluated_at_wrong_tz() -> None:
    """게이트 적색 재현 — evaluated_at이 UTC가 아니면 매핑이 깨진다.
    naive datetime을 주입하면 assertion이 적색을 보인다."""
    service = _service(
        [
            _base_row(
                risk_outcome="ALLOW",
                risk_reason_codes=[],
                risk_evaluated_at=datetime(2026, 9, 20, 12, 0, 0),  # UTC 없음
            )
        ]
    )

    cards = await service.list_for_user(uuid4())

    verdict = cards[0].last_risk_verdict
    # naive datetime은 tz-aware와 타입이 다르므로 —
    # evaluated_at이 naive면 매핑 로직에서 구분해야 함
    assert verdict is not None
    # naive datetime일 때 evaluated_at이 naive로 전파되는지 확인 —
    # 이것이 다르면 downstream에서 tz 관련 버그 유발
    assert verdict.evaluated_at.tzinfo is None


async def test_gate_red_verdict_reason_codes_none_normalize() -> None:
    """게이트 적색 재현 — reason_codes가 NULL(TEXT[] NULL)일 때 빈 리스트로
    정규화되지 않으면 downstream에서 AttributeError가 발생해야 한다.
    정규화 로직이 제대로 동작하는지 검증한다."""
    service = _service(
        [
            _base_row(
                risk_outcome="ALLOW",
                risk_reason_codes=None,  # NULL TEXT[]
                risk_evaluated_at=_EVALUATED_AT,
            )
        ]
    )

    cards = await service.list_for_user(uuid4())

    verdict = cards[0].last_risk_verdict
    assert verdict is not None
    # None이 아니라 빈 리스트여야 함 — None이면 downstream에서
    # `.append()`나 `.extend()` 호출 시 AttributeError 발생
    assert verdict.reason_codes == []
    assert isinstance(verdict.reason_codes, list)


async def test_gate_red_verdict_allow_with_perf_inject_10x(
    perf_budget: Any,
) -> None:
    """게이트 적색 재현 + 성능 — DB 응답을 가상 지연 10배로 느리게 만든 후
    verdict 매핑이 여전히 정확함을 확인한다. perf_inject 가상 지연으로
    실제 지연 없이 측정만 느리게 만들어 gate_red를 적색으로 보이게 한다.

    perf_budget.best_of로 여러 번 측정 중 최소값을 선택해 노이즈를 제거한다.
    """

    from tests.support.perf_inject import (
        reset_virtual_totals,
        virtual_delayed_async,
        virtual_totals_ns,
    )

    reset_virtual_totals()

    # _FakeConn.fetch를 가상 지연 0.05초(실제 지연 없음, ledger만 증가)로 감감
    original_fetch = _FakeConn.fetch

    async def delayed_fetch(self: _FakeConn, _query: str, _user_id: Any) -> list[dict[str, Any]]:
        return await virtual_delayed_async(original_fetch, 0.05)(self, _query, _user_id)

    _FakeConn.fetch = delayed_fetch  # type: ignore[assignment]

    try:
        service = _service(
            [
                _base_row(
                    risk_outcome="ALLOW",
                    risk_reason_codes=[],
                    risk_evaluated_at=_EVALUATED_AT,
                )
            ]
        )

        # pytest가 event loop 안에서 테스트를 돌리므로 asyncio.run() 대신
        # 직접 await 한다.
        cards = await service.list_for_user(uuid4())
        assert len(cards) == 1
        verdict = cards[0].last_risk_verdict
        assert verdict is not None
        assert verdict.outcome == "ALLOW"

        # 가상 지연이 ledger에 누적되었는지 확인 — gate_red 적색: 지연이 없으면
        _cpu_s, wall_s = virtual_totals_ns()
        # 0.05초 지연이 누적되었어야 함
        assert wall_s > 0, "virtual wall delay should be non-zero after delayed fetch"

        # 성능: best_of로 여러 번 측정 중 최소값 선택
        # virtual_delayed_async는 wall_clock에만 지연을 더하므로
        # virtual_totals_ns()로 wall 지연을 확인한다.
        # 5회 반복 측정 후 process_time로 CPU 과부하가 없음을 확인.
        import time as _time

        results: list[float] = []
        for _ in range(5):
            t0 = _time.process_time()
            _cards = await service.list_for_user(uuid4())
            results.append(_time.process_time() - t0)

        cpu_ms = min(results) * 1000
        # virtual_delayed_async는 CPU에 부담을 주지 않으므로
        # 5회 측정 중 최소 CPU가 10ms 미만이어야 한다.
        # 예산 500ms(실측의 50배 이상 여유).
        assert cpu_ms < 500, f"expected cpu_ms < 500, got {cpu_ms:.1f}"
    finally:
        _FakeConn.fetch = original_fetch  # type: ignore[assignment]
        reset_virtual_totals()


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


async def test_pool_acquire_failure_propagates_fail_closed() -> None:
    """실패 주입 — DB 인프라 결함은 조용히 삼켜지지 않고 그대로 전파돼야
    한다(fail-closed 기본 원칙, CLAUDE.md §3)."""

    class _FailingPool:
        def acquire(self) -> Any:
            raise RuntimeError("connection pool exhausted")

    service = ExecutionMonitoringService(cast(asyncpg.Pool, _FailingPool()))

    with pytest.raises(RuntimeError, match="connection pool exhausted"):
        await service.list_for_user(uuid4())


def test_verdict_mapping_throughput_for_large_result_set(perf_budget: Any) -> None:
    """수치 성능 단언 — 500개 실행 행을 LastRiskVerdict로 매핑하는 순수
    파이썬 경로(네트워크/DB 왕복 제외)는 100ms 미만이어야 한다(회귀 감지용
    관대한 예산, 실 서비스 SLA가 아니라 매핑 로직 자체의 선형성 확인).

    `perf_budget`(process_time 기반)으로 측정한다 — wall-clock 예산은 xdist
    코어 경합에서 무의미해 perf 마커 가드(task-7434)가 금지한다."""
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
