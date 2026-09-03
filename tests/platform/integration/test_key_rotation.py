"""PLT-34 `scripts/rotate_credential_keys.py` 통합테스트 — 실 DB(TEST_DATABASE_URL) 대상.

DoD(§9 PLT-34): 100행 회전이 멱등(2회 실행 결과 동일)이고, 중단 후 재실행이
남은 행만 처리해야 한다. LIVE 스코프는 절대 회전 대상이 아니다(PLT-33 §10-8).
"""
from __future__ import annotations

import json
import os

import asyncpg
import pytest

from scripts.rotate_credential_keys import CredentialRotationError, rotate_paper_credentials
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
