"""DEEPEN: tests/foundation/unit/validation/application/__init__.py

Negative / failure-injection coverage for `application/run_check.py` (L41) --
the one application-layer entry point in this package that had zero tests of
its own (`compile_artifact.py` already has `test_compile_artifact.py`;
`start_validation.py` is exercised indirectly elsewhere). No DB needed: both
`ValidationRepository` and `AuditEventRepository` are replaced with
fakes/doubles (mirrors `tests/foundation/unit/validation/checks/
test_backtest.py`'s "no DB, real domain code" style).

DoD checklist (task-10244, orphan leaf task-6704 "고아 산출물 회수 5828
(qa-2)"):
- [x] negative test 3건 이상 추가 (불변식 위반 입력을 명시적으로 거부하는 케이스)
- [x] 실패주입 케이스 1건 이상 추가 (monkeypatch로 의존성 예외 유발 등)
- [x] `python -m pytest tests/foundation/unit/validation/application/__init__.py -q` 통과
- [x] docs/design/INVARIANTS.md 위반 없음
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

import pytest

from src.core.db.conditional_write import ConcurrencyConflictError
from src.data.models.market_data import Candle
from src.foundation.backtest.adapters.list_bars import ListBars
from src.foundation.backtest.api import BacktestConfig, BarSnapshotRef, CostModel, UniverseSnapshot
from src.foundation.backtest.application.run_backtest import BacktestRunError
from src.foundation.validation.application.run_check import (
    CHECK_RUNNERS,
    CheckAlreadyInProgressError,
    UnknownCheckTypeError,
    run_check,
)
from src.foundation.validation.checks import (
    backtest,
    failure_conditions,
    oos_walk_forward,
    point_in_time,
    robustness,
    stress_capacity,
)
from src.foundation.validation.checks.context import CheckContext
from src.foundation.validation.domain.artifact import build_artifact
from src.foundation.validation.domain.models import (
    Outcome,
    RunState,
    ValidationResult,
    ValidationRun,
)
from src.foundation.validation.domain.policy import ValidationPolicy

_T0 = datetime(2026, 1, 1, tzinfo=timezone.utc)


def _bar(index: int) -> Candle:
    ts = _T0 + timedelta(hours=index)
    close = Decimal(100 + index)
    return Candle(
        symbol="BTC/USDT",
        exchange="bitget",
        timeframe="1h",
        open=close,
        high=close,
        low=close,
        close=close,
        volume=Decimal("1"),
        open_time=ts,
        close_time=ts,
    )


def _ctx() -> CheckContext:
    bars = [_bar(i) for i in range(10)]
    return CheckContext(
        artifact=build_artifact(
            strategy_id="strat-1",
            version="v1",
            fsm_definition={"states": ["IDLE"]},
            compiler_version="cc-test-1",
        ),
        policy=ValidationPolicy(),
        bars=ListBars(bars),
        snapshot_ref=BarSnapshotRef(
            snapshot_hash="a" * 64,
            symbol="BTC/USDT",
            exchange="bitget",
            timeframe="1h",
            from_time=bars[0].open_time,
            to_time=bars[-1].close_time,
            bar_count=len(bars),
            source="bitget-rest",
            as_of=bars[-1].close_time,
        ),
        universe=UniverseSnapshot(as_of=bars[-1].close_time, members=[], snapshot_hash="b" * 64),
        config=BacktestConfig(
            strategy_id="strat-1",
            strategy_version="v1",
            initial_equity=Decimal("1000"),
            cost_model=CostModel(fee_bps=Decimal("10"), slippage_bps=Decimal("5")),
            warmup_bars=5,
            periods_per_year=252,
        ),
        seed=0,
        trace_id="trace-1",
        prior_results={},
    )


@dataclass
class _FakeValidationRepo:
    """Duck-typed `ValidationRepository` double. Records every call so a
    test can assert fail-closed behaviour (e.g. "no run was ever created")
    without a real DB."""

    existing_run: ValidationRun | None = None
    existing_result: ValidationResult | None = None
    create_run_raises: Exception | None = None
    create_run_calls: list[dict[str, Any]] = field(default_factory=list)
    mark_failed_calls: list[UUID] = field(default_factory=list)
    complete_with_result_calls: list[UUID] = field(default_factory=list)

    async def get_run_by_snapshot(
        self, strategy_id: str, strategy_version: str, check_type: str, input_snapshot_hash: str
    ) -> ValidationRun | None:
        return self.existing_run

    async def create_run(self, **kwargs: Any) -> ValidationRun:
        self.create_run_calls.append(kwargs)
        if self.create_run_raises is not None:
            raise self.create_run_raises
        return ValidationRun(
            id=uuid4(),
            strategy_id=kwargs["strategy_id"],
            strategy_version=kwargs["strategy_version"],
            check_type=kwargs["check_type"],
            input_snapshot_hash=kwargs["input_snapshot_hash"],
            cost_model=kwargs["cost_model"],
            warmup_bars=kwargs["warmup_bars"],
            periods_per_year=kwargs["periods_per_year"],
            initial_equity=kwargs["initial_equity"],
            state=RunState.QUEUED,
        )

    async def mark_running(self, run_id: UUID) -> ValidationRun:
        return ValidationRun(
            id=run_id,
            strategy_id="strat-1",
            strategy_version="v1",
            check_type="backtest",
            input_snapshot_hash="x",
            cost_model={},
            warmup_bars=5,
            periods_per_year=252,
            initial_equity=Decimal("1000"),
            state=RunState.RUNNING,
        )

    async def mark_failed(self, run_id: UUID) -> ValidationRun:
        self.mark_failed_calls.append(run_id)
        return await self.mark_running(run_id)

    async def complete_with_result(
        self, run_id: UUID, result: ValidationResult
    ) -> tuple[ValidationRun, ValidationResult]:
        self.complete_with_result_calls.append(run_id)
        return await self.mark_running(run_id), result

    async def get_result_for_run(self, run_id: UUID) -> ValidationResult | None:
        return self.existing_result


@dataclass
class _FakeAuditRepo:
    """Duck-typed `AuditEventRepository` double -- only `append_event` is
    exercised by `record_command_event`."""

    append_calls: list[dict[str, Any]] = field(default_factory=list)

    async def append_event(self, **kwargs: Any) -> Any:
        self.append_calls.append(kwargs)
        from src.foundation.evidence.domain.models import AuditEvent

        return AuditEvent(
            id=uuid4(),
            tenant_id=kwargs["tenant_id"],
            sequence_no=1,
            aggregate_type=kwargs["aggregate_type"],
            aggregate_id=kwargs["aggregate_id"],
            aggregate_revision=kwargs["aggregate_revision"],
            action=kwargs["action"],
            outcome=kwargs["outcome"],
            actor_subject_id=kwargs["actor_subject_id"],
            trace_id=kwargs["trace_id"],
            payload_hash=kwargs["payload_hash"],
            payload=kwargs["payload"],
            classification=kwargs["classification"],
            previous_hash=None,
            event_hash="e" * 64,
            occurred_at=datetime.now(timezone.utc),
        )


# -- negative ----------------------------------------------------------------


async def test_unknown_check_type_is_rejected_before_any_repo_write() -> None:
    """불변식: `check_type`이 `CHECK_RUNNERS`(= `policy.REQUIRED_CHECKS`)에
    없으면 fail-closed로 즉시 거부돼야 한다 -- repo에 어떤 쓰기도 일어나기
    전에."""
    repo = _FakeValidationRepo()
    with pytest.raises(UnknownCheckTypeError):
        await run_check(
            repo,
            check_type="not_a_real_check",
            ctx=_ctx(),
            owner_user_id=uuid4(),
        )
    assert repo.create_run_calls == []


async def test_empty_check_type_is_rejected() -> None:
    repo = _FakeValidationRepo()
    with pytest.raises(UnknownCheckTypeError):
        await run_check(repo, check_type="", ctx=_ctx(), owner_user_id=uuid4())


async def test_concurrent_create_without_a_winner_result_signals_in_progress() -> None:
    """STR-007 동급 규칙: 다른 요청이 같은 입력으로 이미 run을 만들었지만 아직
    끝나지 않았으면(결과 없음), 조용히 넘어가지 않고
    `CheckAlreadyInProgressError`로 호출자에게 알려야 한다."""
    winner_run = ValidationRun(
        id=uuid4(),
        strategy_id="strat-1",
        strategy_version="v1",
        check_type="backtest",
        input_snapshot_hash="x",
        cost_model={},
        warmup_bars=5,
        periods_per_year=252,
        initial_equity=Decimal("1000"),
        state=RunState.RUNNING,
    )
    repo = _FakeValidationRepo(
        create_run_raises=ConcurrencyConflictError("unique violation"),
    )

    async def _get_run_by_snapshot_after_race(*args: Any, **kwargs: Any) -> ValidationRun | None:
        return winner_run

    repo.get_run_by_snapshot = _get_run_by_snapshot_after_race
    with pytest.raises(CheckAlreadyInProgressError):
        await run_check(repo, check_type="backtest", ctx=_ctx(), owner_user_id=uuid4())


# -- failure injection ---------------------------------------------------------


async def test_backtest_run_error_marks_run_failed_and_propagates(monkeypatch: Any) -> None:
    """실패주입: `checks.backtest.run`이 `BacktestRunError`를 던지도록
    monkeypatch -- 삼켜지지 않고 그대로 전파되면서, run이 FAILED로
    마크되고 audit 이벤트가 ERROR outcome으로 남아야 한다."""

    def _raise(_ctx: CheckContext) -> Any:
        raise BacktestRunError("injected: insufficient warmup bars")

    monkeypatch.setattr(backtest, "run", _raise)
    monkeypatch.setitem(CHECK_RUNNERS, backtest.CHECK_TYPE, _raise)

    repo = _FakeValidationRepo()
    audit_repo = _FakeAuditRepo()
    with pytest.raises(BacktestRunError, match="injected"):
        await run_check(
            repo,
            check_type="backtest",
            ctx=_ctx(),
            owner_user_id=uuid4(),
            audit_repo=audit_repo,
        )

    assert len(repo.mark_failed_calls) == 1
    assert repo.complete_with_result_calls == []
    assert len(audit_repo.append_calls) == 1
    assert audit_repo.append_calls[0]["action"] == "validation_check_errored:backtest"


# -- baseline / sanity ---------------------------------------------------------


def test_check_runners_table_keys_match_each_modules_own_check_type_constant() -> None:
    """불변식: `CHECK_RUNNERS`의 키가 각 체크 모듈이 선언한 `CHECK_TYPE`과
    항상 일치해야 한다 -- 손으로 복사한 문자열 리터럴이 드리프트하는 걸
    구조적으로 막는다(run_check.py 모듈 docstring 참조)."""
    assert CHECK_RUNNERS[point_in_time.CHECK_TYPE] is point_in_time.run
    assert CHECK_RUNNERS[backtest.CHECK_TYPE] is backtest.run
    assert CHECK_RUNNERS[oos_walk_forward.CHECK_TYPE] is oos_walk_forward.run
    assert CHECK_RUNNERS[robustness.CHECK_TYPE] is robustness.run
    assert CHECK_RUNNERS[stress_capacity.CHECK_TYPE] is stress_capacity.run
    assert CHECK_RUNNERS[failure_conditions.CHECK_TYPE] is failure_conditions.run


async def test_cached_run_with_existing_result_short_circuits_without_rerunning() -> None:
    """STR-001 멱등성: 같은 입력 조합의 run+result가 이미 있으면, 그 결과를
    그대로 돌려주고 새 run을 만들지 않는다."""
    existing_run = ValidationRun(
        id=uuid4(),
        strategy_id="strat-1",
        strategy_version="v1",
        check_type="backtest",
        input_snapshot_hash="x",
        cost_model={},
        warmup_bars=5,
        periods_per_year=252,
        initial_equity=Decimal("1000"),
        state=RunState.SUCCEEDED,
    )
    existing_result = ValidationResult(
        id=uuid4(),
        run_id=existing_run.id,
        outcome=Outcome.PASS,
        metrics={},
    )
    repo = _FakeValidationRepo(existing_run=existing_run, existing_result=existing_result)
    result = await run_check(repo, check_type="backtest", ctx=_ctx(), owner_user_id=uuid4())
    assert result is existing_result
    assert repo.create_run_calls == []
