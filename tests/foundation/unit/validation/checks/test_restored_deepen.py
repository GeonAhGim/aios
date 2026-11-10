"""DEEPEN: tests/foundation/unit/validation/checks/__init__.py

Negative / failure-injection tests for the package-level contracts that
`src.foundation.validation.checks` and its registry
(`application/run_check.CHECK_RUNNERS`) must hold: `REQUIRED_CHECKS`
(`domain/policy.py`) is the single source of truth for which six checks
exist, `CHECK_RUNNERS` must mirror it exactly, and `run_check` must reject
an unknown check type before touching any repository I/O, and must
propagate (not swallow) a runner's own exception.

DoD checklist (task-10245, orphan leaf task-6704 "고아 산출물 회수 5828 (qa-2)"):
- [x] negative test 3건 이상 추가 (불변식 위반 입력을 명시적으로 거부하는 케이스)
- [x] 실패주입 케이스 1건 이상 추가 (monkeypatch로 의존성 예외 유발 등)
- [x] `python -m pytest tests/foundation/unit/validation/checks/__init__.py -q` 통과
- [x] docs/design/INVARIANTS.md 위반 없음
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest

from src.foundation.backtest.application.run_backtest import BacktestRunError
from src.foundation.validation.application import run_check as run_check_module
from src.foundation.validation.application.run_check import (
    CHECK_RUNNERS,
    UnknownCheckTypeError,
    run_check,
)
from src.foundation.validation.domain.policy import REQUIRED_CHECKS


class TestRequiredChecksNegative:
    """REQUIRED_CHECKS가 spec의 고정 6개 집합에서 벗어나지 않는지 거부 검증."""

    def test_required_checks_count_is_six(self) -> None:
        """spec §2 row 156/165는 정확히 6개 검사를 요구한다 -- 개수가 드리프트하면
        정책/러너 테이블이 조용히 불완전해진다."""
        assert len(REQUIRED_CHECKS) == 6

    def test_no_duplicate_check_names(self) -> None:
        """중복된 이름이 있으면 같은 검사가 두 번 등록/실행될 수 있다 -- 불변식 위반."""
        assert len(set(REQUIRED_CHECKS)) == len(REQUIRED_CHECKS)

    def test_bogus_check_name_not_required(self) -> None:
        """임의의 존재하지 않는 이름이 REQUIRED_CHECKS에 섞여 들어가지 않았는지 확인한다."""
        assert "nonexistent_check" not in REQUIRED_CHECKS


class TestCheckRunnersNegative:
    """CHECK_RUNNERS 레지스트리가 REQUIRED_CHECKS와 정확히 일치하는지 거부 검증."""

    def test_check_runners_keys_match_required_checks(self) -> None:
        """CHECK_RUNNERS의 키 집합이 REQUIRED_CHECKS와 다르면, 어떤 검사는 실행할
        방법이 없거나(누락) 정책에 없는 검사가 몰래 실행 가능해진다(과잉)."""
        assert set(CHECK_RUNNERS) == set(REQUIRED_CHECKS)

    def test_bogus_check_type_not_in_runners(self) -> None:
        """등록되지 않은 check_type은 CHECK_RUNNERS에 없어야 한다."""
        assert "nonexistent_check" not in CHECK_RUNNERS

    async def test_unknown_check_type_rejected_before_repo_io(self) -> None:
        """`run_check`은 미등록 check_type을 레포지토리에 접근하기 전에 즉시
        거부해야 한다 -- 그래야 잘못된 타입 하나가 QUEUED 행을 만들고 버려지는
        고아 run을 남기지 않는다. repo를 `None`으로 넘겨도 통과한다는 사실 자체가
        '레포 접근 이전에 거부'를 증명한다."""
        no_repo: Any = None
        no_ctx: Any = None
        with pytest.raises(UnknownCheckTypeError, match="nonexistent_check"):
            await run_check(
                validation_repo=no_repo,
                check_type="nonexistent_check",
                ctx=no_ctx,
                owner_user_id=uuid4(),
            )


class TestRunCheckFailureInjection:
    """실패주입 -- 러너 자신의 예외가 run_check 밖으로 전파되는지(삼켜지지 않는지) 검증."""

    async def test_runner_exception_propagates_and_marks_failed(self) -> None:
        """체크 러너가 `BacktestRunError`를 던지면(`checks/backtest.py`의 docstring이
        명시하는 "정책 판단 이전의 재생-실행 실패") `run_check`은 그 예외를
        감추지 않고 그대로 전파해야 하며, 그 전에 run을 FAILED로 표시해야 한다
        -- 삼키면 호출자가 성공으로 착각하는 거짓 양성이 생긴다."""
        run_id = uuid4()
        fake_run = SimpleNamespace(id=run_id)
        fake_repo: Any = SimpleNamespace(
            get_run_by_snapshot=AsyncMock(return_value=None),
            create_run=AsyncMock(return_value=fake_run),
            mark_running=AsyncMock(return_value=fake_run),
            mark_failed=AsyncMock(return_value=fake_run),
        )
        ctx: Any = SimpleNamespace(
            artifact=SimpleNamespace(strategy_id="strat-1", version="v1", artifact_hash="a" * 64),
            policy=SimpleNamespace(policy_hash=lambda: "p" * 64),
            snapshot_ref=SimpleNamespace(snapshot_hash="s" * 64),
            seed=0,
            config=SimpleNamespace(
                cost_model=SimpleNamespace(fee_bps="10", slippage_bps="5"),
                warmup_bars=0,
                periods_per_year=252,
                initial_equity="1000",
            ),
        )

        def _raise_backtest_run_error(_ctx: object) -> object:
            raise BacktestRunError("simulated: insufficient warmup bars")

        with patch.dict(run_check_module.CHECK_RUNNERS, {"backtest": _raise_backtest_run_error}):
            with pytest.raises(BacktestRunError, match="simulated: insufficient warmup bars"):
                await run_check(
                    validation_repo=fake_repo,
                    check_type="backtest",
                    ctx=ctx,
                    owner_user_id=uuid4(),
                )

        fake_repo.mark_failed.assert_awaited_once_with(run_id)
