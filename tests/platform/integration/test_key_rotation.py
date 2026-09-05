"""PLT-34 `scripts/rotate_credential_keys.py` 통합테스트 — 실 DB(TEST_DATABASE_URL) 대상.

DoD(§9 PLT-34): 100행 회전이 멱등(2회 실행 결과 동일)이고, 중단 후 재실행이
남은 행만 처리해야 한다. LIVE 스코프는 절대 회전 대상이 아니다(PLT-33 §10-8).
"""
from __future__ import annotations

import json
import os

import asyncpg
import pytest

from scripts.rotate_credential_keys import (
    CredentialRotationError,
    _run,
    build_parser,
    count_pending_paper_credentials,
    rotate_paper_credentials,
)
from src.core.observability.metric_names import SECURITY_KEY_ROTATION_COUNT_TOTAL
from src.core.observability.metrics import NullMetrics, set_metrics
from src.core.security.encryption import decrypt, encrypt
from src.core.security.key_ring import KeyRing
from tests.integration.conftest import create_test_user

_KEYS = {"k1": bytes.fromhex("11" * 32), "k2": bytes.fromhex("22" * 32)}
OLD_RING = KeyRing(_KEYS, active_kid="k1")
NEW_RING = KeyRing(_KEYS, active_kid="k2")


def _asyncpg_dsn() -> str:
    return os.environ["DATABASE_URL"].replace("postgresql+asyncpg://", "postgresql://")


@pytest.fixture
async def pool():
    p = await asyncpg.create_pool(_asyncpg_dsn(), min_size=1, max_size=4)
    yield p
    await p.close()


@pytest.fixture(autouse=True)
async def _exchange_credentials_clean_slate(pool):
    """`rotate_paper_credentials`는 `scope='PAPER'` 전체를 대상으로 조회하므로,
    다른 테스트가 같은 TEST_DATABASE_URL에 남긴 `exchange_credentials` 행이
    있으면 이 파일의 회전 건수 단언이 어긋난다 — 매 테스트 전후로 비운다."""

    async def _clean() -> None:
        async with pool.acquire() as conn:
            await conn.execute("DELETE FROM exchange_credentials")

    await _clean()
    yield
    await _clean()


async def _seed_rows(
    pool: asyncpg.Pool, count: int, *, scope: str = "PAPER", ring: KeyRing = OLD_RING
) -> list[int]:
    ids: list[int] = []
    async with pool.acquire() as conn:
        for i in range(count):
            user_id = await create_test_user(pool)
            row = await conn.fetchrow(
                "INSERT INTO exchange_credentials "
                "(user_id, exchange, scope, key_version, "
                " api_key_encrypted, api_secret_encrypted, extra_encrypted) "
                "VALUES ($1, 'bitget', $2, $3, $4, $5, $6) RETURNING id",
                user_id,
                scope,
                ring.active_kid,
                encrypt(f"key-{i}", ring).encode("ascii"),
                encrypt(f"secret-{i}", ring).encode("ascii"),
                encrypt(json.dumps({"n": i}), ring).encode("ascii"),
            )
            ids.append(row["id"])
    return ids


async def _row(pool: asyncpg.Pool, row_id: int) -> asyncpg.Record:
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT api_key_encrypted, api_secret_encrypted, extra_encrypted, key_version, scope "
            "FROM exchange_credentials WHERE id = $1",
            row_id,
        )
    assert row is not None
    return row


async def test_full_batch_rotates_and_round_trips(pool):
    ids = await _seed_rows(pool, 20)

    total = await rotate_paper_credentials(pool, NEW_RING, batch_size=100)

    assert total == 20
    for i, row_id in enumerate(ids):
        row = await _row(pool, row_id)
        assert row["key_version"] == "k2"
        assert decrypt(row["api_key_encrypted"].decode("ascii"), NEW_RING) == f"key-{i}"
        assert decrypt(row["api_secret_encrypted"].decode("ascii"), NEW_RING) == f"secret-{i}"
        assert json.loads(decrypt(row["extra_encrypted"].decode("ascii"), NEW_RING)) == {"n": i}


async def test_second_run_is_idempotent(pool):
    await _seed_rows(pool, 15)

    first = await rotate_paper_credentials(pool, NEW_RING, batch_size=100)
    second = await rotate_paper_credentials(pool, NEW_RING, batch_size=100)

    assert first == 15
    assert second == 0


async def test_interrupted_run_resumes_with_only_remaining_rows(pool):
    ids = await _seed_rows(pool, 100)

    partial = await rotate_paper_credentials(pool, NEW_RING, batch_size=10, max_rows=40)
    assert partial == 40

    rotated_now = 0
    for row_id in ids:
        row = await _row(pool, row_id)
        if row["key_version"] == "k2":
            rotated_now += 1
    assert rotated_now == 40

    rest = await rotate_paper_credentials(pool, NEW_RING, batch_size=10)
    assert rest == 60

    for row_id in ids:
        row = await _row(pool, row_id)
        assert row["key_version"] == "k2"


async def test_live_scope_rows_are_never_rotated(pool):
    [paper_id] = await _seed_rows(pool, 1, scope="PAPER")
    [live_id] = await _seed_rows(pool, 1, scope="LIVE")

    total = await rotate_paper_credentials(pool, NEW_RING, batch_size=100)

    assert total == 1
    assert (await _row(pool, paper_id))["key_version"] == "k2"
    live_row = await _row(pool, live_id)
    assert live_row["key_version"] == "k1"
    assert live_row["scope"] == "LIVE"


async def test_missing_old_kid_fails_without_leaking_plaintext_and_leaves_row_untouched(pool):
    [row_id] = await _seed_rows(pool, 1)
    incomplete_ring = KeyRing({"k2": _KEYS["k2"]}, active_kid="k2")

    with pytest.raises(CredentialRotationError) as exc_info:
        await rotate_paper_credentials(pool, incomplete_ring, batch_size=100)

    assert "key-0" not in str(exc_info.value)
    assert "secret-0" not in str(exc_info.value)
    row = await _row(pool, row_id)
    assert row["key_version"] == "k1"


async def test_rotation_writes_audit_log_without_plaintext(pool):
    [row_id] = await _seed_rows(pool, 1)

    await rotate_paper_credentials(pool, NEW_RING, batch_size=100)

    async with pool.acquire() as conn:
        audit_row = await conn.fetchrow(
            "SELECT action_type, decision_data FROM audit_log "
            "WHERE action_type = 'security.key_rotated' AND target_id = $1",
            str(row_id),
        )
    assert audit_row is not None
    assert "key-0" not in audit_row["decision_data"]
    assert "secret-0" not in audit_row["decision_data"]


class _SpyMetrics:
    def __init__(self) -> None:
        self.counters: list[tuple[str, dict[str, str] | None]] = []

    def counter(self, name: str, labels: dict[str, str] | None = None) -> None:
        self.counters.append((name, labels))

    def observe(self, name: str, value: float, labels: dict[str, str] | None = None) -> None:
        return None

    def gauge(self, name: str, value: float, labels: dict[str, str] | None = None) -> None:
        return None


async def test_exception_mid_batch_keeps_progress_and_rerun_finishes_exactly_once(pool):
    """실제 예외로 중단(40번째 행의 kid가 KeyRing에 없음) → 앞 행은 커밋, 실패 행은
    롤백, 뒤 행은 미처리. 원인 제거 후 재실행하면 남은 행만 처리하고 감사 이벤트는
    행당 정확히 1건(이중 회전·이중 감사 없음). outcome=failure 메트릭 1건."""
    spy = _SpyMetrics()
    set_metrics(spy)
    try:
        orphan_ring = KeyRing({"k9": bytes.fromhex("99" * 32)}, active_kid="k9")
        first_ids = await _seed_rows(pool, 40)
        [orphan_id] = await _seed_rows(pool, 1, ring=orphan_ring)
        last_ids = await _seed_rows(pool, 59)

        with pytest.raises(CredentialRotationError):
            await rotate_paper_credentials(pool, NEW_RING, batch_size=10)

        for row_id in first_ids:
            assert (await _row(pool, row_id))["key_version"] == "k2"
        assert (await _row(pool, orphan_id))["key_version"] == "k9"
        for row_id in last_ids:
            assert (await _row(pool, row_id))["key_version"] == "k1"
        failures = [c for c in spy.counters if c[1] and c[1].get("outcome") == "failure"]
        assert failures == [
            (SECURITY_KEY_ROTATION_COUNT_TOTAL, {"scope": "PAPER", "outcome": "failure"})
        ]

        repaired_ring = KeyRing({**_KEYS, "k9": bytes.fromhex("99" * 32)}, active_kid="k2")
        rest = await rotate_paper_credentials(pool, repaired_ring, batch_size=10)
        assert rest == 60

        async with pool.acquire() as conn:
            audit_rows = await conn.fetch(
                "SELECT target_id, count(*) AS n FROM audit_log "
                "WHERE action_type = 'security.key_rotated' AND target_id = ANY($1::text[]) "
                "GROUP BY target_id",
                [str(i) for i in first_ids + [orphan_id] + last_ids],
            )
        assert len(audit_rows) == 100
        assert all(r["n"] == 1 for r in audit_rows)
        successes = [c for c in spy.counters if c[1] and c[1].get("outcome") == "success"]
        assert len(successes) == 100
    finally:
        set_metrics(NullMetrics())


async def test_dry_run_counts_pending_without_writing(pool):
    ids = await _seed_rows(pool, 7)
    await _seed_rows(pool, 1, scope="LIVE")

    assert await count_pending_paper_credentials(pool, NEW_RING) == 7
    for row_id in ids:
        assert (await _row(pool, row_id))["key_version"] == "k1"


async def test_cli_run_wires_key_ring_from_env_and_database_url(pool, monkeypatch, capsys):
    """I-10 배선 증명: `_run`이 실제로 `KeyRing.from_env("PAPER")`와 DATABASE_URL을
    써서 회전한다(테스트 본체가 함수를 직접 호출하는 것과 별개로 CLI 경로 확인)."""
    monkeypatch.setenv(
        "CREDENTIAL_ENCRYPTION_KEYS_PAPER", f"k1:{'11' * 32},k2:{'22' * 32}"
    )
    monkeypatch.setenv("CREDENTIAL_ENCRYPTION_ACTIVE_KID_PAPER", "k2")
    monkeypatch.delenv("CREDENTIAL_ENCRYPTION_KEYS_LIVE", raising=False)
    monkeypatch.delenv("CREDENTIAL_ENCRYPTION_ACTIVE_KID_LIVE", raising=False)
    ids = await _seed_rows(pool, 3)

    assert await _run(batch_size=2, max_rows=None, dry_run=True) == 0
    assert "3건" in capsys.readouterr().out
    for row_id in ids:
        assert (await _row(pool, row_id))["key_version"] == "k1"

    assert await _run(batch_size=2, max_rows=None) == 0
    out = capsys.readouterr().out
    assert "총 3건" in out
    assert "key-0" not in out and "secret-0" not in out
    for i, row_id in enumerate(ids):
        row = await _row(pool, row_id)
        assert row["key_version"] == "k2"
        assert decrypt(row["api_key_encrypted"].decode("ascii"), NEW_RING) == f"key-{i}"


def test_cli_rejects_non_positive_batch_and_max_rows():
    parser = build_parser()
    with pytest.raises(SystemExit):
        parser.parse_args(["--batch-size", "0"])
    with pytest.raises(SystemExit):
        parser.parse_args(["--max-rows", "-1"])
    args = parser.parse_args(["--dry-run"])
    assert args.dry_run is True and args.batch_size == 100 and args.max_rows is None
