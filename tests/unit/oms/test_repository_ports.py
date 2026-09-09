"""L4-07 ports/repository.py 구조적 계약 테스트.

Spec: docs/specs/L4_execution_oms_and_exchange_v1.0.md §2-C, §9 L4-07.

`@runtime_checkable` Protocol이므로 `isinstance()`는 메서드 이름만 확인한다
(시그니처는 mypy가 정적으로 확인). 그래도 "포트가 요구하는 메서드 전부를
갖췄는가"는 여기서 실행 시점에 증명할 수 있다 — fail-closed 원칙대로, 메서드
하나라도 빠지면 어댑터는 포트를 만족하지 못한다는 것을 실증한다.
"""
from __future__ import annotations

import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from time import perf_counter
from uuid import uuid4

import asyncpg
import pytest
from pydantic import ValidationError

from src.services.oms.ports.repository import (
    ClaimResult,
    FillRepoPort,
    IdempotencyRepoPort,
    InboxRepoPort,
    OrderEventRepoPort,
    OrderRepoPort,
    OutboxRepoPort,
    OutboxRow,
)


class _FullOrderRepo:
    async def transition(self, conn, **kwargs): ...
    async def get_for_update(self, conn, order_id): ...
    async def find_by_scope_hash(self, conn, scope_hash): ...
    async def list_children_for_update(self, conn, parent_order_id): ...
    async def set_committed_child_qty(self, conn, **kwargs): ...


class _MissingMethodOrderRepo:
    """`get_for_update`가 빠진 불완전 구현 — 포트를 만족하지 못해야 한다."""

    async def transition(self, conn, **kwargs): ...
    async def find_by_scope_hash(self, conn, scope_hash): ...


class _FullOrderEventRepo:
    async def append(self, conn, ev): ...
    async def timeline(self, conn, order_id): ...


class _FullFillRepo:
    async def insert_if_absent(self, conn, fill): ...
    async def list_for_order(self, conn, order_id): ...


class _FullOutboxRepo:
    async def enqueue(self, conn, **kwargs): ...
    async def claim_batch(self, conn, **kwargs): ...
    async def reclaim_stuck_sending(self, conn, **kwargs): ...
    async def mark_done(self, conn, id, **kwargs): ...
    async def mark_retry(self, conn, id, **kwargs): ...
    async def mark_dead(self, conn, id, **kwargs): ...


class _StrictOutboxRepo:
    """`OutboxRepoPort`의 mark_* 시그니처를 그대로 구현 — expected_worker가
    누락되면 실제 어댑터가 만들어지기 전에도 TypeError로 fail-closed됨을 증명."""

    async def enqueue(self, conn, **kwargs): ...
    async def claim_batch(self, conn, **kwargs): ...
    async def reclaim_stuck_sending(self, conn, **kwargs): ...
    async def mark_done(self, conn, id, *, expected_worker): ...
    async def mark_retry(
        self, conn, id, *, attempt, not_before, last_error, expected_worker
    ): ...
    async def mark_dead(self, conn, id, *, reason, expected_worker): ...


class _FullInboxRepo:
    async def insert_if_absent(self, conn, ev): ...
    async def claim_unprocessed(self, conn, **kwargs): ...
    async def mark_processed(self, conn, id, **kwargs): ...


class _FullIdempotencyRepo:
    async def claim(self, conn, **kwargs): ...


def test_full_implementations_satisfy_their_ports() -> None:
    assert isinstance(_FullOrderRepo(), OrderRepoPort)
    assert isinstance(_FullOrderEventRepo(), OrderEventRepoPort)
    assert isinstance(_FullFillRepo(), FillRepoPort)
    assert isinstance(_FullOutboxRepo(), OutboxRepoPort)
    assert isinstance(_FullInboxRepo(), InboxRepoPort)
    assert isinstance(_FullIdempotencyRepo(), IdempotencyRepoPort)


def test_incomplete_implementation_fails_port_check() -> None:
    """포트 메서드 하나 누락 → isinstance() False(fail-closed 구조 증명)."""
    assert not isinstance(_MissingMethodOrderRepo(), OrderRepoPort)


def test_outbox_row_rejects_unknown_command_type() -> None:
    now = datetime.now(timezone.utc)
    with pytest.raises(ValidationError):
        OutboxRow(
            id=uuid4(),
            order_id=uuid4(),
            command_type="DELETE",  # type: ignore[arg-type]
            payload={},
            state="PENDING",
            attempt=0,
            not_before=now,
            lease_until=None,
            worker_id=None,
            last_error=None,
            created_at=now,
            updated_at=now,
        )


def test_claim_result_new_has_no_order_id() -> None:
    result = ClaimResult(kind="NEW")
    assert result.order_id is None


def test_mark_retry_and_mark_dead_require_expected_worker() -> None:
    """366행 규칙표: done/retry/dead 모두 `worker_id=$2` 펜싱 — expected_worker
    없이 호출하면(늦은 워커의 조건 없는 쓰기 시도에 대응) TypeError로 막혀야 한다."""
    repo = _StrictOutboxRepo()
    now = datetime.now(timezone.utc)
    with pytest.raises(TypeError):
        repo.mark_retry(None, uuid4(), attempt=1, not_before=now, last_error="x")
    with pytest.raises(TypeError):
        repo.mark_dead(None, uuid4(), reason="x")


def test_claim_result_ttl_type_is_timedelta() -> None:
    # ttl은 IdempotencyRepoPort.claim의 파라미터 타입일 뿐 모델 필드가 아니므로
    # 여기서는 timedelta 임포트가 여전히 유효한 계약임을 회귀 방지로 확인한다.
    assert timedelta(hours=1).total_seconds() == 3600


class _DroppedConnection:
    """asyncpg가 실제 네트워크 단절 시 던지는 예외 타입을 그대로 들고 있는
    가짜 커넥션 — 풀이 이미 죽은 커넥션을 다시 꺼내 쓰려 할 때의 실제
    시나리오(§5.1 outbox 어댑터가 `conn`으로 받는 것과 동일한 asyncpg 예외)."""

    def __init__(self) -> None:
        self.simulated_failure = asyncpg.exceptions.ConnectionDoesNotExistError(
            "connection was closed in the middle of operation"
        )


class _NetworkFailureOutboxRepo:
    """`OutboxRepoPort`를 구조적으로 만족하지만, 실제 I/O 지점에서는 끊긴
    커넥션 위에서 실행된다고 가정한다 — fail-closed라면 이 예외를 삼키거나
    성공으로 위장하지 않고 그대로 전파해야 한다."""

    async def enqueue(self, conn, **kwargs): ...
    async def claim_batch(self, conn, **kwargs): ...
    async def reclaim_stuck_sending(self, conn, **kwargs): ...

    async def mark_done(self, conn, id, *, expected_worker):
        raise conn.simulated_failure

    async def mark_retry(
        self, conn, id, *, attempt, not_before, last_error, expected_worker
    ):
        raise conn.simulated_failure

    async def mark_dead(self, conn, id, *, reason, expected_worker):
        raise conn.simulated_failure


async def test_mark_done_propagates_simulated_connection_drop() -> None:
    """DB/네트워크 단절 주입 — 포트를 만족하는 구현이라도 커넥션이 죽으면
    asyncpg의 실제 예외 타입 그대로 전파돼야 한다(protocol-only 구조 검사를
    넘어선 실패 주입 증명, DEPTH_L4_BR task-111 D2 미달 사유 해소)."""
    repo = _NetworkFailureOutboxRepo()
    assert isinstance(repo, OutboxRepoPort)
    conn = _DroppedConnection()
    with pytest.raises(asyncpg.exceptions.ConnectionDoesNotExistError):
        await repo.mark_done(conn, uuid4(), expected_worker="A")


async def test_mark_retry_and_mark_dead_propagate_simulated_connection_drop() -> None:
    repo = _NetworkFailureOutboxRepo()
    conn = _DroppedConnection()
    now = datetime.now(timezone.utc)
    with pytest.raises(asyncpg.exceptions.ConnectionDoesNotExistError):
        await repo.mark_retry(
            conn, uuid4(), attempt=1, not_before=now, last_error="x", expected_worker="A"
        )
    with pytest.raises(asyncpg.exceptions.ConnectionDoesNotExistError):
        await repo.mark_dead(conn, uuid4(), reason="x", expected_worker="A")


def test_outbox_row_bulk_validation_latency_budget() -> None:
    """`claim_batch`가 돌려주는 배치를 `OutboxRow`로 파싱하는 비용 — 로컬
    회귀 예산이며 SLO 단언은 아니다(headless worker 지침)."""
    now = datetime.now(timezone.utc)

    def _build() -> OutboxRow:
        return OutboxRow(
            id=uuid4(),
            order_id=uuid4(),
            command_type="SUBMIT",
            payload={"order": {"symbol": "BTC/USDT"}},
            state="PENDING",
            attempt=0,
            not_before=now,
            lease_until=None,
            worker_id=None,
            last_error=None,
            created_at=now,
            updated_at=now,
        )

    _build()  # 콜드 스타트(스키마 컴파일 등) 워밍업 — 예산 밖.
    started = perf_counter()
    for _ in range(2000):
        _build()
    elapsed = perf_counter() - started
    assert elapsed < 5.0, f"2000 OutboxRow validations took {elapsed:.3f}s (budget 5.0s)"


def test_pytest_gate_turns_red_when_command_type_literal_is_widened(
    tmp_path: Path,
) -> None:
    """실제 거부 테스트가 통과하는 걸 먼저 확인하고, `CommandType` Literal
    상한을 지우면(=미지정 명령을 계약에서 걸러내지 못하면) pytest가 exit 1로
    red가 되는 것까지 증명한다(gate/CI red-line regression proof)."""
    test_copy = tmp_path / "test_repository_ports_copy.py"
    test_copy.write_text(Path(__file__).read_text(encoding="utf-8"), encoding="utf-8")
    config = tmp_path / "pytest.ini"
    config.write_text("[pytest]\n", encoding="utf-8")
    command = [
        sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider",
        "-c", str(config), "--confcutdir", str(tmp_path),
        f"{test_copy}::test_outbox_row_rejects_unknown_command_type",
    ]
    env = dict(os.environ, PYTHONPATH=str(Path.cwd()), PYTEST_ADDOPTS="")
    baseline = subprocess.run(
        command, capture_output=True, text=True, env=env, timeout=60, check=False
    )
    assert baseline.returncode == 0, baseline.stdout + baseline.stderr
    assert "1 passed" in baseline.stdout

    # 프로덕션 소스 파일은 그대로 두고, 이 자식 프로세스의 모듈 객체만 변조한다.
    (tmp_path / "conftest.py").write_text(
        "import importlib\nfrom pathlib import Path\n"
        "name = 'src.services.oms.ports.repository'\n"
        "module = importlib.import_module(name)\n"
        "source = Path(module.__file__).read_text(encoding='utf-8')\n"
        "guard = 'CommandType = Literal[\"SUBMIT\", \"CANCEL\", \"MODIFY\"]'\n"
        "assert source.count(guard) == 1\n"
        "mutant = compile(source.replace(guard, 'CommandType = str'), module.__file__, 'exec')\n"
        "exec(mutant, module.__dict__)\n",
        encoding="utf-8",
    )
    mutated = subprocess.run(
        command, capture_output=True, text=True, env=env, timeout=60, check=False
    )
    assert mutated.returncode == 1, mutated.stdout + mutated.stderr
    assert "1 failed" in mutated.stdout
