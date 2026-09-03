"""PLT-34 — `exchange_credentials` PAPER 자격증명 키 회전 CLI.

Spec: docs/specs/L4_platform_observability_tenancy_api_v1.0.md#§9 PLT-34
(+ RB-05). `key_version`이 `KeyRing.active_kid`와 다른 PAPER 행을 찾아
api_key/api_secret/extra를 옛 kid로 복호하고 새 kid로 재암호화한다
(`src.core.security.encryption` PLT-31 포맷 재사용).

미검증/PM 확인 필요: task decision은 "PLT-32 rewrap(wrapped_dek만 재래핑,
본문 재암호화 없음)만 사용"을 요구하지만, `exchange_credentials`는 레코드를
KeyRing 키로 직접 암호화하는 PLT-31 포맷(`aios1$<kid>$b64`)이고 별도
wrapped_dek 컬럼이 없다 — envelope.rewrap이 적용될 대상 자체가 이 스키마에
존재하지 않는다. 따라서 이 스크립트는 옛 kid로 복호 → 새 kid로 재암호화하는
경로를 쓴다(본문까지 다시 암호화됨, PLT-32 decision이 피하려던 비용이 여기선
발생). 평문은 각 행 처리 중 지역 변수로만 잠깐 존재하고 로그·예외 메시지에는
절대 싣지 않는다.

행 하나 = 트랜잭션 하나 + `key_version` 조건부 UPDATE(105번 표준)라서 중단 후
재실행은 이미 회전된 행을 다시 건드리지 않고 남은 행만 처리한다(멱등).
LIVE 스코프 행은 조회 대상에서 아예 제외한다(PLT-33 §10-8, ADR-2026-08-29-E).
"""
from __future__ import annotations

import argparse
import asyncio
import os

import asyncpg

from src.core.logging.audit_log import record_audit_log
from src.core.observability.metric_names import SECURITY_KEY_ROTATION_COUNT_TOTAL
from src.core.observability.metrics import metrics
from src.core.security.encryption import decrypt, encrypt
from src.core.security.key_ring import KeyRing

_SCOPE = "PAPER"
_ACTOR = "system:rotate_credential_keys"


class CredentialRotationError(Exception):
    """행 복호/재암호화 실패. 원인 메시지에 평문 값은 절대 담지 않는다."""


def _asyncpg_dsn() -> str:
    url = os.environ["DATABASE_URL"]
    return url.replace("postgresql+asyncpg://", "postgresql://")


async def _select_pending_ids(conn: asyncpg.Connection, active_kid: str, limit: int) -> list[int]:
    rows = await conn.fetch(
        "SELECT id FROM exchange_credentials "
        "WHERE scope = $1 AND key_version <> $2 ORDER BY id LIMIT $3",
        _SCOPE,
        active_kid,
        limit,
    )
    return [row["id"] for row in rows]


async def _rotate_row(
    conn: asyncpg.Connection, key_ring: KeyRing, row_id: int, active_kid: str
) -> bool:
    """행 하나를 트랜잭션 하나로 회전. 이미 회전됐거나(다른 실행이 선점) 사라졌으면
    아무것도 하지 않고 False를 반환한다(재실행 시 이중 카운트 방지)."""
    async with conn.transaction():
        row = await conn.fetchrow(
            "SELECT api_key_encrypted, api_secret_encrypted, extra_encrypted, key_version "
            "FROM exchange_credentials WHERE id = $1 AND scope = $2 FOR UPDATE",
            row_id,
            _SCOPE,
        )
        if row is None or row["key_version"] == active_kid:
            return False
        old_kid = row["key_version"]
        try:
            api_key = decrypt(row["api_key_encrypted"].decode("ascii"), key_ring)
            api_secret = decrypt(row["api_secret_encrypted"].decode("ascii"), key_ring)
            extra_raw = row["extra_encrypted"]
            extra_plain = (
                decrypt(extra_raw.decode("ascii"), key_ring) if extra_raw is not None else None
            )
            new_key = encrypt(api_key, key_ring).encode("ascii")
            new_secret = encrypt(api_secret, key_ring).encode("ascii")
            new_extra = (
                encrypt(extra_plain, key_ring).encode("ascii") if extra_plain is not None else None
            )
        except Exception as exc:
            raise CredentialRotationError(
                f"id={row_id} 복호/재암호화 실패(kid={old_kid!r})"
            ) from exc

        result = await conn.execute(
            "UPDATE exchange_credentials SET api_key_encrypted = $1, "
            "api_secret_encrypted = $2, extra_encrypted = $3, key_version = $4 "
            "WHERE id = $5 AND scope = $6 AND key_version = $7",
            new_key,
            new_secret,
            new_extra,
            active_kid,
            row_id,
            _SCOPE,
            old_kid,
        )
        if result != "UPDATE 1":
            return False

        await record_audit_log(
            conn,
            actor_agent=_ACTOR,
            action_type="security.key_rotated",
            target_type="exchange_credentials",
            target_id=str(row_id),
            decision_data={"scope": _SCOPE, "from_kid": old_kid, "to_kid": active_kid},
        )
    metrics().counter(SECURITY_KEY_ROTATION_COUNT_TOTAL, {"scope": _SCOPE, "outcome": "success"})
    return True


async def rotate_paper_credentials(
    pool: asyncpg.Pool,
    key_ring: KeyRing,
    *,
    batch_size: int = 100,
    max_rows: int | None = None,
) -> int:
    """대상 행이 없거나 `max_rows`에 도달할 때까지 배치 단위로 회전한다.
    반환값은 실제로 회전한 행 수 — 중단 후 재호출하면 남은 행부터 이어진다."""
    active_kid = key_ring.active_kid
    total = 0
    async with pool.acquire() as conn:
        while max_rows is None or total < max_rows:
            limit = batch_size if max_rows is None else min(batch_size, max_rows - total)
            pending = await _select_pending_ids(conn, active_kid, limit)
            if not pending:
                break
            for row_id in pending:
                if await _rotate_row(conn, key_ring, row_id, active_kid):
                    total += 1
                if max_rows is not None and total >= max_rows:
                    break
            print(f"회전 진행: {total}건 완료.")
    return total


async def _run(*, batch_size: int, max_rows: int | None) -> int:
    key_ring = KeyRing.from_env(_SCOPE)
    pool = await asyncpg.create_pool(_asyncpg_dsn(), min_size=1, max_size=4)
    try:
        total = await rotate_paper_credentials(
            pool, key_ring, batch_size=batch_size, max_rows=max_rows
        )
        print(f"완료: 총 {total}건 회전(scope={_SCOPE}, active_kid={key_ring.active_kid}).")
        return 0
    finally:
        await pool.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--batch-size", type=int, default=100, help="배치당 조회 행 수")
    parser.add_argument("--max-rows", type=int, default=None, help="이번 실행 최대 처리 행 수")
    args = parser.parse_args()
    return asyncio.run(_run(batch_size=args.batch_size, max_rows=args.max_rows))


if __name__ == "__main__":
    raise SystemExit(main())
