"""PLT-34 — `exchange_credentials` PAPER 자격증명 키 회전 CLI.

Spec: docs/specs/L4_platform_observability_tenancy_api_v1.0.md#§9 PLT-34
(+ RB-05). `key_version`이 `KeyRing.active_kid`와 다른 PAPER 행을 찾아
api_key/api_secret/extra를 옛 kid로 복호하고 새 kid로 재암호화한다
(`src.core.security.encryption` PLT-31 포맷 재사용).

실행은 저장소 루트에서 모듈 형태로만: `python -m scripts.rotate_credential_keys`
(`python scripts/rotate_credential_keys.py` 직접 실행은 sys.path에 `scripts/`만
실려 `ModuleNotFoundError: src` — scripts/export_openapi.py와 동일 규칙).

decrypt→re-encrypt 방식은 PM 사후 승인(task-1542 decision): `exchange_credentials`는
레코드를 KeyRing 키로 직접 암호화하는 PLT-31 포맷(`aios1$<kid>$b64`)이고 별도
wrapped_dek 컬럼이 없어 PLT-32 `envelope.rewrap`을 적용할 대상이 없다(봉투 포맷
전환은 M6 확장 마이그레이션이 필요한 별도 리프·CA ADR). 그 대가로 평문이 행
처리 중 잠깐 존재하므로 (1) 평문은 bytearray 버퍼에만 담고 finally에서 zero-fill,
(2) decrypt/encrypt/UPDATE/감사 INSERT 예외는 원인 예외를 체인하지 않고 행 id·kid·
스테이지·예외 타입명만 담은 `CredentialRotationError`로 재포장한다(원인 예외의
traceback 프레임이 평문 인자를 붙들 수 있어 로그·stderr로 새는 경로를 차단).

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
from src.core.security.key_ring import KeyRing, SecretScope

_SCOPE: SecretScope = "PAPER"
_ACTOR = "system:rotate_credential_keys"


class CredentialRotationError(Exception):
    """행 회전 실패(복호·재암호화·UPDATE·감사 INSERT). 메시지는 행 id·kid·스테이지·
    원인 예외 타입명만 담고 원인 예외를 체인하지 않는다(평문 미노출)."""


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


def _reencrypt(row: asyncpg.Record, key_ring: KeyRing) -> tuple[bytes, bytes, bytes | None]:
    """옛 kid 암호문 3개 → 새 kid 암호문. 평문은 이 함수 안의 bytearray 버퍼에만
    존재하고 성공·실패 무관하게 finally에서 zero-fill 후 참조를 끊는다. `encrypt`에
    넘기는 str은 호출 동안만 사는 임시값(Python str은 zeroize 불가 — §2.2
    secret_handle의 정직한 한계와 동일)이며, 실패 시 호출부가 원인 예외를 버리므로
    그 프레임에 남은 임시값도 함께 해제된다."""
    buffers: list[bytearray] = []

    def _decrypt_into(token: bytes) -> bytearray:
        buf = bytearray(decrypt(token.decode("ascii"), key_ring).encode("utf-8"))
        buffers.append(buf)
        return buf

    try:
        api_key = _decrypt_into(row["api_key_encrypted"])
        api_secret = _decrypt_into(row["api_secret_encrypted"])
        extra_raw = row["extra_encrypted"]
        extra = _decrypt_into(extra_raw) if extra_raw is not None else None
        new_key = encrypt(api_key.decode("utf-8"), key_ring).encode("ascii")
        new_secret = encrypt(api_secret.decode("utf-8"), key_ring).encode("ascii")
        new_extra = (
            encrypt(extra.decode("utf-8"), key_ring).encode("ascii") if extra is not None else None
        )
        return new_key, new_secret, new_extra
    finally:
        for buf in buffers:
            buf[:] = bytes(len(buf))
        buffers.clear()


async def _rotate_row(
    conn: asyncpg.Connection, key_ring: KeyRing, row_id: int, active_kid: str
) -> bool:
    """행 하나를 트랜잭션 하나로 회전. 이미 회전됐거나(다른 실행이 선점) 사라졌으면
    아무것도 하지 않고 False를 반환한다(재실행 시 이중 카운트 방지). 어느 스테이지든
    예외면 트랜잭션 롤백 + outcome=failure 메트릭 + 레닥션된 CredentialRotationError."""
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
        stage, cause = "reencrypt", ""
        try:
            new_key, new_secret, new_extra = _reencrypt(row, key_ring)
            stage = "update"
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
            stage = "audit"
            await record_audit_log(
                conn,
                actor_agent=_ACTOR,
                action_type="security.key_rotated",
                target_type="exchange_credentials",
                target_id=str(row_id),
                decision_data={"scope": _SCOPE, "from_kid": old_kid, "to_kid": active_kid},
            )
        except Exception as exc:  # noqa: BLE001 — 원인 예외는 타입명만 남기고 버린다(평문 체인 금지)
            cause = type(exc).__name__
        if cause:
            # except 블록 밖에서 raise → __cause__/__context__ 모두 비어 traceback에
            # 원인 예외(및 그 프레임의 평문 인자)가 실리지 않는다.
            metrics().counter(
                SECURITY_KEY_ROTATION_COUNT_TOTAL, {"scope": _SCOPE, "outcome": "failure"}
            )
            raise CredentialRotationError(
                f"id={row_id} {stage} 실패(kid={old_kid!r}, cause={cause})"
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


async def count_pending_paper_credentials(pool: asyncpg.Pool, key_ring: KeyRing) -> int:
    """`--dry-run`(§2.2 계약): 회전 대상(PAPER, key_version <> active_kid) 행 수만
    센다. 아무것도 쓰지 않는다."""
    async with pool.acquire() as conn:
        count: int = await conn.fetchval(
            "SELECT count(*) FROM exchange_credentials WHERE scope = $1 AND key_version <> $2",
            _SCOPE,
            key_ring.active_kid,
        )
    return count


async def _run(*, batch_size: int, max_rows: int | None, dry_run: bool = False) -> int:
    key_ring = KeyRing.from_env(_SCOPE)
    pool = await asyncpg.create_pool(_asyncpg_dsn(), min_size=1, max_size=4)
    try:
        if dry_run:
            pending = await count_pending_paper_credentials(pool, key_ring)
            print(
                f"dry-run: 회전 대상 {pending}건(scope={_SCOPE}, "
                f"active_kid={key_ring.active_kid}). 아무것도 변경하지 않았습니다."
            )
            return 0
        total = await rotate_paper_credentials(
            pool, key_ring, batch_size=batch_size, max_rows=max_rows
        )
        print(f"완료: 총 {total}건 회전(scope={_SCOPE}, active_kid={key_ring.active_kid}).")
        return 0
    finally:
        await pool.close()


def _positive_int(raw: str) -> int:
    value = int(raw)
    if value < 1:
        raise argparse.ArgumentTypeError(f"1 이상의 정수여야 합니다: {raw}")
    return value


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--batch-size", type=_positive_int, default=100, help="배치당 조회 행 수(1 이상)"
    )
    parser.add_argument(
        "--max-rows", type=_positive_int, default=None, help="이번 실행 최대 처리 행 수(1 이상)"
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="회전 대상 행 수만 출력하고 아무것도 쓰지 않는다"
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return asyncio.run(
        _run(batch_size=args.batch_size, max_rows=args.max_rows, dry_run=args.dry_run)
    )


if __name__ == "__main__":
    raise SystemExit(main())
