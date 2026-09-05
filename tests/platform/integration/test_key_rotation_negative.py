"""PLT-34 배선·negative 테스트(task-1544, 리뷰 task-1542 REJECT 수정분) — 실 DB 대상.

(1) I-10 배선: 저장소 루트에서 subprocess로 `python -m scripts.rotate_credential_keys`를
    실제 실행한다(PYTHONPATH 제거 — pytest의 sys.path 우회 없음). RB-05 절차 1·2의
    명령이 그대로 exit 0 + 처리 건수 stdout을 내야 한다.
(2) 정상 복호 후 encrypt가 평문을 되뇌는 예외로 실패해도 예외 메시지·traceback·로그·
    stdout/stderr 어디에도 평문이 없다(원인 예외 체인 없음 → 프레임 해제).
(3) 감사 INSERT가 실제 DB 오류로 실패하면 outcome=failure 메트릭이 집계되고 행은 롤백.
시드·조회 헬퍼는 `test_key_rotation.py`의 것을 재사용한다(파일 300줄 상한으로 분리).
"""
from __future__ import annotations

import logging
import os
import subprocess
import sys
import traceback
from pathlib import Path

import asyncpg
import pytest

from scripts import rotate_credential_keys as rck
from scripts.rotate_credential_keys import CredentialRotationError, rotate_paper_credentials
from src.core.observability.metric_names import SECURITY_KEY_ROTATION_COUNT_TOTAL
from src.core.observability.metrics import NullMetrics, set_metrics
from src.core.security.key_ring import KeyRing
from tests.platform.integration.test_key_rotation import (
    NEW_RING,
    _asyncpg_dsn,
    _row,
    _seed_rows,
    _SpyMetrics,
)

_REPO_ROOT = Path(__file__).resolve().parents[3]
_PLAINTEXTS = ("key-0", "secret-0", '"n": 0')  # _seed_rows(pool, 1)이 심는 평문 3종


@pytest.fixture
async def pool():
    p = await asyncpg.create_pool(_asyncpg_dsn(), min_size=1, max_size=4)
    yield p
    await p.close()


@pytest.fixture(autouse=True)
async def _exchange_credentials_clean_slate(pool):
    async with pool.acquire() as conn:
        await conn.execute("DELETE FROM exchange_credentials")
    yield
    async with pool.acquire() as conn:
        await conn.execute("DELETE FROM exchange_credentials")


@pytest.fixture
def spy_metrics():
    spy = _SpyMetrics()
    set_metrics(spy)
    yield spy
    set_metrics(NullMetrics())


def _failures(spy: _SpyMetrics) -> list[tuple[str, dict[str, str] | None]]:
    return [c for c in spy.counters if c[1] and c[1].get("outcome") == "failure"]


def _cli_env() -> dict[str, str]:
    """subprocess 환경: pytest가 만든 PYTHONPATH·LIVE 키를 제거하고 PAPER KeyRing만 준다."""
    dropped = {
        "PYTHONPATH",
        "CREDENTIAL_ENCRYPTION_KEYS_LIVE",
        "CREDENTIAL_ENCRYPTION_ACTIVE_KID_LIVE",
    }
    env = {k: v for k, v in os.environ.items() if k not in dropped}
    env["CREDENTIAL_ENCRYPTION_KEYS_PAPER"] = f"k1:{'11' * 32},k2:{'22' * 32}"
    env["CREDENTIAL_ENCRYPTION_ACTIVE_KID_PAPER"] = "k2"
    env["PYTHONIOENCODING"] = "utf-8"
    return env


def _run_cli(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "scripts.rotate_credential_keys", *args],
        cwd=_REPO_ROOT,
        env=_cli_env(),
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=90,
        check=False,
    )


async def test_cli_module_form_runs_from_repo_root_without_pytest_sys_path(pool):
    """RB-05 절차 1(--dry-run)·2(회전)의 명령을 실제 프로세스로 실행 — I-10 배선 증명."""
    ids = await _seed_rows(pool, 3)

    dry = _run_cli("--dry-run")
    assert dry.returncode == 0, dry.stderr
    assert "ModuleNotFoundError" not in dry.stderr
    assert "3건" in dry.stdout
    for row_id in ids:
        assert (await _row(pool, row_id))["key_version"] == "k1"

    real = _run_cli("--batch-size", "2", "--max-rows", "2")
    assert real.returncode == 0, real.stderr
    assert "총 2건" in real.stdout
    for secret in _PLAINTEXTS:
        assert secret not in real.stdout + real.stderr
    versions = sorted([(await _row(pool, row_id))["key_version"] for row_id in ids])
    assert versions == ["k1", "k2", "k2"]


async def test_encrypt_failure_after_decrypt_never_exposes_plaintext(
    pool, monkeypatch, caplog, capsys, spy_metrics
):
    """복호는 성공(평문이 실제로 메모리에 존재)한 뒤 encrypt가 평문을 되뇌는 예외로
    실패 — 재포장 예외 메시지·렌더된 traceback·로그·stdout/stderr에 평문 부재."""
    [row_id] = await _seed_rows(pool, 1)

    def leaky_encrypt(plaintext: str, ring: KeyRing) -> str:
        raise RuntimeError(f"library echoed its input: {plaintext}")

    monkeypatch.setattr(rck, "encrypt", leaky_encrypt)

    with caplog.at_level(logging.DEBUG), pytest.raises(CredentialRotationError) as exc_info:
        await rotate_paper_credentials(pool, NEW_RING, batch_size=100)

    err = exc_info.value
    rendered = "".join(traceback.format_exception(err))
    captured = capsys.readouterr()
    for secret in _PLAINTEXTS:
        assert secret not in str(err)
        assert secret not in rendered
        assert secret not in caplog.text
        assert secret not in captured.out + captured.err
    assert err.__cause__ is None and err.__context__ is None
    assert str(err) == f"id={row_id} reencrypt 실패(kid='k1', cause=RuntimeError)"
    assert (await _row(pool, row_id))["key_version"] == "k1"
    assert _failures(spy_metrics) == [
        (SECURITY_KEY_ROTATION_COUNT_TOTAL, {"scope": "PAPER", "outcome": "failure"})
    ]


async def test_audit_insert_db_error_counts_failure_metric_and_rolls_back(
    pool, monkeypatch, spy_metrics
):
    """UPDATE는 성공했지만 같은 트랜잭션의 감사 INSERT가 실제 DB 오류로 실패 →
    failure 메트릭 1건, success 0건, 행은 롤백(구 kid 유지), 감사 행 없음."""
    [row_id] = await _seed_rows(pool, 1)

    async def failing_audit(conn: asyncpg.Connection, **kwargs: object) -> None:
        await conn.execute("INSERT INTO audit_log_missing_table (id) VALUES (1)")

    monkeypatch.setattr(rck, "record_audit_log", failing_audit)

    with pytest.raises(CredentialRotationError) as exc_info:
        await rotate_paper_credentials(pool, NEW_RING, batch_size=100)

    assert str(exc_info.value) == f"id={row_id} audit 실패(kid='k1', cause=UndefinedTableError)"
    assert (await _row(pool, row_id))["key_version"] == "k1"
    assert _failures(spy_metrics) == [
        (SECURITY_KEY_ROTATION_COUNT_TOTAL, {"scope": "PAPER", "outcome": "failure"})
    ]
    assert not [c for c in spy_metrics.counters if c[1] and c[1].get("outcome") == "success"]
    async with pool.acquire() as conn:
        audit_count = await conn.fetchval(
            "SELECT count(*) FROM audit_log "
            "WHERE action_type = 'security.key_rotated' AND target_id = $1",
            str(row_id),
        )
    assert audit_count == 0
