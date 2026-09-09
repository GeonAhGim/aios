"""EO-03 배선 증명(I-10) — ExecutionLoopScheduler 시그니처 필수화·리스 필터.

Spec: docs/specs/L4_execution_ownership_and_safety_gate_wiring_v1.0.md §2-B,
§3.3(Before/After), §4.1, §4.2. docs/design/INVARIANTS.md I-01·I-02·I-10.

tests/integration/test_execution_scheduler.py는 실DB 위에서 "리스를 남이 쥔
execution은 tick되지 않는다"를 증명하지만, 주입한 `pre_submit_gate`/
`distrust_monitor` *객체 그 자체*가 `run_execution_tick(...)`까지 도달하는지는
단언하지 않는다(allow-all 게이트라 도달 여부와 무관하게 통과한다). I-10은
"구현됨 ≠ 작동함"이므로 여기서 `run_execution_tick`을 기록용 가짜로 바꿔
넘겨받은 kwargs가 주입 객체와 동일(identity)임을 직접 증명한다. 그 외:

- 필수 인자(pre_submit_gate/distrust_monitor/lease_repo/owner_id) 중 하나라도
  빠지면 생성 자체가 TypeError — §4.2 "생성 자체가 불가능"의 런타임 증거.
- `list_candidates()`는 RUNNING/PAPER 전부를 리포지토리에 넘기고 반환 집합에
  든 것만 돌려준다. ttl은 지정 없으면 `interval_sec × 5`(§5.2 Draft), 지정하면
  그 값이 그대로 전달된다.
- 리스를 하나도 못 얻으면 `run_execution_tick`은 호출되지 않고 report의
  ticked/failed/skipped 전부 비어 있다(§4.1 "예외 없이 이번 주기 건너뜀").
- RUNNING 행이 0건이면 리포지토리 왕복 자체를 하지 않는다.
"""
from __future__ import annotations

import uuid
from typing import Any

import pytest

from src.core.loader.risk_policy_loader import load_risk_policy
from src.core.safety.data_distrust import DataDistrustMonitor
from src.services.execution_loop import scheduler as scheduler_module
from src.services.execution_loop.scheduler import ExecutionLoopScheduler
from src.services.order_service.gate import GateDecision, GateOutcome, OrderContext

_SPEC_TTL_MULTIPLIER = 5  # §5.2 Draft — interval_sec의 5배


class _FakeConn:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self._rows = rows

    async def fetch(self, query: str, *args: object) -> list[dict[str, Any]]:
        assert "status = 'RUNNING'" in query and "mode = 'PAPER'" in query
        return self._rows


class _FakeAcquire:
    def __init__(self, conn: _FakeConn) -> None:
        self._conn = conn

    async def __aenter__(self) -> _FakeConn:
        return self._conn

    async def __aexit__(self, *exc: object) -> None:
        return None


class _FakePool:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self._conn = _FakeConn(rows)

    def acquire(self) -> _FakeAcquire:
        return _FakeAcquire(self._conn)


class _RecordingLeaseRepo:
    """ExecutionLeaseRepository 포트 — `grant`에 든 id만 획득 성공으로 돌려준다."""

    def __init__(self, grant: set[int]) -> None:
        self.grant = grant
        self.calls: list[dict[str, Any]] = []

    async def acquire_or_renew_many(
        self, execution_ids: list[int], *, owner_id: str, ttl_seconds: float
    ) -> set[int]:
        self.calls.append(
            {"execution_ids": list(execution_ids), "owner_id": owner_id, "ttl_seconds": ttl_seconds}
        )
        return {i for i in execution_ids if i in self.grant}

    async def release_all(self, owner_id: str) -> int:
        return 0


async def _allow_all(_context: OrderContext) -> GateDecision:
    return GateDecision(
        outcome=GateOutcome.ALLOW, decision_id=uuid.uuid4(), compliance_decision_id=uuid.uuid4()
    )


async def _resolve_adapter(user_id: uuid.UUID, exchange: str) -> Any:
    return object()


def _rows(*ids: int) -> list[dict[str, Any]]:
    return [{"id": i, "user_id": uuid.uuid4(), "exchange": "bitget"} for i in ids]


def _required_kwargs(lease_repo: _RecordingLeaseRepo | None = None) -> dict[str, Any]:
    return dict(
        resolve_adapter=_resolve_adapter,
        policy=load_risk_policy(),
        pre_submit_gate=_allow_all,
        distrust_monitor=DataDistrustMonitor(),
        lease_repo=lease_repo or _RecordingLeaseRepo(set()),
        owner_id=f"unit-owner-{uuid.uuid4().hex[:8]}",
    )


def _install_recording_tick(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    captured: list[dict[str, Any]] = []

    async def _fake_run_execution_tick(
        pool: Any, adapter: Any, execution_id: int, **kwargs: Any
    ) -> None:
        captured.append({"execution_id": execution_id, **kwargs})

    monkeypatch.setattr(scheduler_module, "run_execution_tick", _fake_run_execution_tick)
    return captured


# --- I-01 / §4.2: 생성 자체가 불가능 ------------------------------------------


@pytest.mark.parametrize(
    "missing", ["pre_submit_gate", "distrust_monitor", "lease_repo", "owner_id"]
)
def test_constructor_rejects_missing_safety_argument(missing: str) -> None:
    kwargs = _required_kwargs()
    del kwargs[missing]
    with pytest.raises(TypeError, match=missing):
        ExecutionLoopScheduler(_FakePool([]), **kwargs)


# --- I-10: 주입 객체가 run_execution_tick까지 동일 객체로 도달 ------------------


async def test_tick_one_forwards_injected_gate_and_monitor(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured = _install_recording_tick(monkeypatch)
    monitor = DataDistrustMonitor()

    async def _gate(_context: OrderContext) -> GateDecision:
        return GateDecision(
            outcome=GateOutcome.ALLOW, decision_id=uuid.uuid4(), compliance_decision_id=uuid.uuid4()
        )

    kwargs = _required_kwargs(_RecordingLeaseRepo({7}))
    kwargs.update(pre_submit_gate=_gate, distrust_monitor=monitor)
    scheduler = ExecutionLoopScheduler(_FakePool(_rows(7)), **kwargs)

    report = await scheduler.tick_all_running()

    assert report.ticked == [7] and report.failed == {}
    assert len(captured) == 1
    assert captured[0]["pre_submit_gate"] is _gate
    assert captured[0]["distrust_monitor"] is monitor


# --- I-02 / §4.1: 리스 필터 ------------------------------------------------------


async def test_list_candidates_returns_only_leased_and_forwards_owner_and_default_ttl() -> None:
    repo = _RecordingLeaseRepo({1, 3})
    kwargs = _required_kwargs(repo)
    scheduler = ExecutionLoopScheduler(_FakePool(_rows(1, 2, 3)), **kwargs)

    candidates = await scheduler.list_candidates()

    assert [row["id"] for row in candidates] == [1, 3]
    assert len(repo.calls) == 1
    assert repo.calls[0]["execution_ids"] == [1, 2, 3]
    assert repo.calls[0]["owner_id"] == kwargs["owner_id"]
    interval = kwargs["policy"].execution_loop.interval_sec
    assert repo.calls[0]["ttl_seconds"] == interval * _SPEC_TTL_MULTIPLIER


async def test_lease_ttl_override_is_passed_through() -> None:
    repo = _RecordingLeaseRepo({1})
    scheduler = ExecutionLoopScheduler(
        _FakePool(_rows(1)), ttl_override_seconds=12.5, **_required_kwargs(repo)
    )

    await scheduler.list_candidates()

    assert repo.calls[0]["ttl_seconds"] == 12.5


async def test_unleased_execution_is_neither_ticked_nor_failed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """negative — 리스를 못 얻으면 tick 경로에 진입조차 하지 않는다(§4.1)."""
    captured = _install_recording_tick(monkeypatch)
    scheduler = ExecutionLoopScheduler(
        _FakePool(_rows(11, 12)), **_required_kwargs(_RecordingLeaseRepo(set()))
    )

    report = await scheduler.tick_all_running()

    assert captured == []
    assert report.ticked == [] and report.failed == {} and report.skipped_no_credential == []


async def test_no_running_rows_skips_lease_repository_roundtrip() -> None:
    repo = _RecordingLeaseRepo({1})
    scheduler = ExecutionLoopScheduler(_FakePool([]), **_required_kwargs(repo))

    assert await scheduler.list_candidates() == []
    assert repo.calls == []
