"""LB-15 `compute_daily_nav` 진짜 failure-injection + 게이트 적색 재현.

`test_compute_daily_nav.py`에서 분리(task-10197, 675줄 LOC 규율 초과).

1. `test_pool_connection_lost_before_write_propagates_and_writes_nothing` —
   `compute_daily_nav`가 읽기 단계(1차 `pool.acquire()`)와 쓰기 단계(2차
   `pool.acquire()`) 사이에서 실제로 두 번 커넥션을 새로 얻는다는 사실을
   이용해, 두 번째 획득에서 진짜 asyncpg 예외(`ConnectionDoesNotExistError`)를
   던지는 얇은 래퍼로 실제 커넥션 유실을 재현한다 — 예외가 감싸이지 않고
   그대로 전파되는지, 그리고 부분 쓰기(고아 행)가 없는지를 실 DB 조회로
   확인한다.
2. `test_pytest_gate_turns_red_when_verify_chain_call_is_removed` —
   `tests/integration/foundation/market_data/test_replay_candles.py`
   (task-2972)와 동일 기법(자식 pytest 프로세스에 소스 문자열 치환 주입)으로
   `verify_chain` 호출을 지우면 기존 체인 위반 negative test가 green에서
   red로 뒤집힘을 실측한다.
"""

from __future__ import annotations

import importlib
import os
import subprocess
import sys
from decimal import Decimal
from pathlib import Path

import asyncpg
import pytest

from src.foundation.positions.adapters.postgres_nav_repository import PostgresNavRepository
from src.foundation.positions.adapters.postgres_snapshot_repository import (
    PostgresSnapshotRepository,
)
from src.foundation.positions.application.compute_daily_nav import compute_daily_nav
from tests.integration.foundation.positions._compute_daily_nav_fixtures import (
    BITGET,
    NOW,
    FakeCashSource,
    FakeFxRateSource,
    cmd,
    setup_account,
)


class _FlakyPool:
    def __init__(self, pool: asyncpg.Pool, *, fail_at_acquire: int, error: Exception) -> None:
        self._pool = pool
        self._fail_at = fail_at_acquire
        self._error = error
        self._count = 0

    def acquire(self) -> asyncpg.pool.PoolAcquireContext:
        self._count += 1
        if self._count == self._fail_at:
            raise self._error
        return self._pool.acquire()


async def test_pool_connection_lost_before_write_propagates_and_writes_nothing(
    pool: asyncpg.Pool,
) -> None:
    tenant_id, account_id = await setup_account(pool)
    cash = FakeCashSource()
    cash.seed(account_id, Decimal("1000"))
    nav_repo = PostgresNavRepository(pool)
    flaky_pool = _FlakyPool(
        pool,
        fail_at_acquire=2,  # 1차 acquire(읽기)는 통과, 2차 acquire(쓰기)에서 유실
        error=asyncpg.exceptions.ConnectionDoesNotExistError("simulated connection loss"),
    )

    with pytest.raises(asyncpg.exceptions.ConnectionDoesNotExistError):
        await compute_daily_nav(
            cmd(tenant_id=tenant_id, account_id=account_id, at=NOW),
            snapshots=PostgresSnapshotRepository(pool),
            cash=cash,
            nav_repo=nav_repo,
            calendar=BITGET,
            fx=FakeFxRateSource(),
            pool=flaky_pool,
        )

    async with pool.acquire() as conn:
        stored = await nav_repo.get(conn, account_id, BITGET.trading_day_of(NOW))
    assert stored is None, "커넥션 유실 시도가 고아 행을 남겼습니다"


_THIS_TESTFILE = "tests/integration/foundation/positions/test_compute_daily_nav.py"

_VERIFY_CHAIN_GUARD = (
    "    nav.verify_chain(\n"
    "        prev_nav if prev_nav is not None else "
    "_genesis(cmd.account_id, nav_date, cmd.base_currency),\n"
    "        candidate,\n"
    "    )\n"
)
_VERIFY_CHAIN_MUTATED = ""


def _source_mutation_plugin_source(module_name: str, guard: str, mutated: str) -> str:
    return f"""\
import importlib
from pathlib import Path


def pytest_configure(config):
    module = importlib.import_module({module_name!r})
    source = Path(module.__file__).read_text(encoding="utf-8")
    guard = {guard!r}
    assert source.count(guard) == 1
    mutated_src = source.replace(guard, {mutated!r})
    mutant = compile(mutated_src, module.__file__, "exec")
    exec(mutant, module.__dict__)
"""


def _run_pytest_node(
    target_test: str, *, plugin_name: str | None = None, plugin_dir: Path | None = None
) -> subprocess.CompletedProcess[str]:
    repo_root = str(Path.cwd())
    command = [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", target_test]
    env = dict(
        os.environ,
        PYTHONPATH=repo_root,
        PYTEST_ADDOPTS="",
        PYTHONIOENCODING="utf-8",
        TEST_DATABASE_URL=os.environ["DATABASE_URL"],
    )
    # The child reuses this worker's DB; it must not DROP/reclone the parent's DB.
    env.pop("PYTEST_XDIST_WORKER", None)
    if plugin_name is not None:
        assert plugin_dir is not None
        command = [*command[:-1], "-p", plugin_name, command[-1]]
        env["PYTHONPATH"] = f"{repo_root}{os.pathsep}{plugin_dir}"
    return subprocess.run(
        command,
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        env=env,
        timeout=180,
        check=False,
    )


def test_pytest_gate_turns_red_when_verify_chain_call_is_removed(tmp_path: Path) -> None:
    """게이트 적색 재현 — LB-15의 핵심 계약(체인 등식 위반을 저장 전에
    거부한다, task-714 DoD)을 지우면(verify_chain 호출 제거),
    `test_chain_break_when_rollforward_does_not_reconcile_is_rejected`가
    green에서 red로 뒤집혀야 한다 — 이 negative test가 실제로 그 회귀를
    잡는다는 증명(I-10)."""
    module = importlib.import_module("src.foundation.positions.application.compute_daily_nav")
    assert module.__file__ is not None
    source = Path(module.__file__).read_text(encoding="utf-8")
    assert source.count(_VERIFY_CHAIN_GUARD) == 1

    target_test = (
        f"{_THIS_TESTFILE}::test_chain_break_when_rollforward_does_not_reconcile_is_rejected"
    )
    baseline = _run_pytest_node(target_test)
    assert baseline.returncode == 0, baseline.stdout + baseline.stderr
    assert "1 passed" in baseline.stdout

    plugin_name = "_mutate_compute_daily_nav_verify_chain"
    plugin_path = tmp_path / f"{plugin_name}.py"
    plugin_path.write_text(
        _source_mutation_plugin_source(
            "src.foundation.positions.application.compute_daily_nav",
            _VERIFY_CHAIN_GUARD,
            _VERIFY_CHAIN_MUTATED,
        ),
        encoding="utf-8",
    )

    mutated = _run_pytest_node(target_test, plugin_name=plugin_name, plugin_dir=tmp_path)
    assert mutated.returncode != 0, mutated.stdout + mutated.stderr
    assert "1 passed" not in mutated.stdout
    assert "1 failed" in mutated.stdout
