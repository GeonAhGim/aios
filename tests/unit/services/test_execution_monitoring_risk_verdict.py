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

import time
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, cast
from uuid import uuid4

import asyncpg
import pytest

from src.services.execution_monitoring_service import ExecutionMonitoringService

_EVALUATED_AT = datetime(2026, 9, 20, 12, 0, 0, tzinfo=timezone.utc)


def _base_row(**overrides: Any) -> dict[str, Any]:
    row = {
        "execution_id": 1,
        "strategy_id": "test-strategy",
        "strategy_version": "1.0.0",
        "status": "RUNNING",
        "mode": "PAPER",
        "exchange": "bitget",
        "allocated_capital": Decimal("500"),
        "started_at": datetime.now(timezone.utc) - timedelta(days=2),
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


async def test_pool_acquire_failure_propagates_fail_closed() -> None:
    """실패 주입 — DB 인프라 결함은 조용히 삼켜지지 않고 그대로 전파돼야
    한다(fail-closed 기본 원칙, CLAUDE.md §3)."""

    class _FailingPool:
        def acquire(self) -> Any:
            raise RuntimeError("connection pool exhausted")

    service = ExecutionMonitoringService(cast(asyncpg.Pool, _FailingPool()))

    with pytest.raises(RuntimeError, match="connection pool exhausted"):
        await service.list_for_user(uuid4())


async def test_verdict_mapping_throughput_for_large_result_set() -> None:
    """수치 성능 단언 — 500개 실행 행을 LastRiskVerdict로 매핑하는 순수
    파이썬 경로(네트워크/DB 왕복 제외)는 100ms 미만이어야 한다(회귀 감지용
    관대한 예산, 실 서비스 SLA가 아니라 매핑 로직 자체의 선형성 확인)."""
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

    start = time.perf_counter()
    cards = await service.list_for_user(uuid4())
    elapsed = time.perf_counter() - start

    assert len(cards) == 500
    assert elapsed < 0.1
