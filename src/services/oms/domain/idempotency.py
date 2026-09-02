"""멱등 스코프·digest·안정적 client id 생성(L4 명세 §2-A, R4).

Spec: docs/specs/L4_execution_oms_and_exchange_v1.0.md §2-A, §9 L4-03.

R4 — "멱등성 스코프가 전역이 아님" — tenant/account/provider/strategy/
version/time-window 단위로 좁힌다(기존 `client_order_id` UNIQUE 하나뿐이던
전역 스코프 결함의 근본 수정).
"""
from __future__ import annotations

import hashlib
from datetime import datetime
from uuid import UUID

from src.services.oms.contracts.v1_commands import IdempotencyScope, SubmitOrderCommand


def build_scope(
    *,
    tenant_id: UUID,
    account_ref: str,
    provider: str,
    strategy_id: str,
    strategy_version: str,
    execution_id: int,
    intent_seq: int,
    window_start: datetime,
) -> IdempotencyScope:
    return IdempotencyScope(
        tenant_id=tenant_id,
        account_ref=account_ref,
        provider=provider,
        strategy_id=strategy_id,
        strategy_version=strategy_version,
        execution_id=execution_id,
        intent_seq=intent_seq,
        window_start=window_start,
    )


def scope_hash(scope: IdempotencyScope) -> str:
    """sha256 hex(64자) — `order_idempotency.scope_hash` UNIQUE 제약의
    실제 값(R4). 필드 순서를 명시적으로 고정해 pydantic 내부 표현 변경에
    영향받지 않는다."""
    payload = "|".join(
        [
            scope.schema_version,
            str(scope.tenant_id),
            scope.account_ref,
            scope.provider,
            scope.strategy_id,
            scope.strategy_version,
            str(scope.execution_id),
            str(scope.intent_seq),
            scope.window_start.isoformat(),
        ]
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def command_digest(cmd: SubmitOrderCommand) -> str:
    """같은 scope_hash로 재제출된 두 명령이 실제로 같은 내용인지 확인하는
    값(§3.4 OMS_IDEMPOTENCY_DIGEST_MISMATCH). `command_id`/`trace_id`/
    `actor_subject_id`/`issued_at`는 시도마다 달라도 되는 필드라 제외한다
    — 이 넷만 다르고 나머지가 같으면 "같은 주문의 재시도"로 취급해야
    하기 때문이다."""
    payload = "|".join(
        [
            cmd.symbol,
            cmd.side.value,
            cmd.order_type.value,
            str(cmd.quantity),
            str(cmd.price) if cmd.price is not None else "",
            cmd.time_in_force,
            cmd.asset_class.value,
            cmd.mode,
            str(cmd.parent_order_id) if cmd.parent_order_id is not None else "",
            str(cmd.algo_run_id) if cmd.algo_run_id is not None else "",
            str(cmd.is_liquidation),
        ]
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def client_order_id(scope: IdempotencyScope, *, max_len: int, charset: str) -> str:
    """결정론적 client id — 같은 scope는 항상 같은 id를 낸다(R1 "재시도마다
    새 키가 생기던" 결함의 근본 수정). `charset`으로 제한된 문자만 쓰고
    `max_len`을 넘지 않는다(venue별 제약, §3.2 `client_order_id_max_len`/
    `client_order_id_charset`)."""
    if max_len <= 0:
        raise ValueError("max_len은 1 이상이어야 합니다.")
    if not charset:
        raise ValueError("charset은 비어 있을 수 없습니다.")

    digest_bytes = hashlib.sha256(scope_hash(scope).encode("utf-8")).digest()
    value = int.from_bytes(digest_bytes, "big")
    base = len(charset)

    chars: list[str] = []
    while value > 0 and len(chars) < max_len:
        value, remainder = divmod(value, base)
        chars.append(charset[remainder])
    if not chars:
        chars.append(charset[0])

    return "".join(reversed(chars))
