"""OMS `OrderIdempotencyScope` 개명(task-1815, PLT-45) DEEPEN — task-3189.

Spec: docs/specs/L4_platform_observability_tenancy_api_v1.0.md §9 PLT-45,
ADR-2026-09-06-G §10.

원 리프(f6986bbb, task-1815 "IdempotencyScope -> OrderIdempotencyScope")는
순수 개명 커밋이라 신규 negative·실패주입·성능·게이트 테스트가 전혀 없었다
(D2 하한 미달, ADR-2026-09-09-C Decision 1). 개명이 실제로 고치는 결함은
"`src/api/contracts/idempotency.py`의 `IdempotencyScope`(헤더/라우트/
다이제스트 스코프)와 `src/services/oms/contracts/v1_commands.py`의
`IdempotencyScope`(주문 재시도 스코프)가 완전히 다른 필드 집합인데 같은
이름이라, 잘못된 쪽을 임포트해도 타입검사가 통과하고 멱등성 의미가 조용히
바뀐다"이다 — 이 파일은 그 위험이 지금은 실제로 막혀 있음을 실행 가능한
증거로 만든다: 두 타입이 서로의 kwargs를 받아들이지 않고(negative),
직렬화 경계를 넘어 잘못 배달돼도 fail-closed며(failure-injection), 순수
계산 경로(scope_hash/client_order_id/command_digest) 성능에 회귀가 없다
(성능 단언). `scripts/check_audit_regressions.py`의
`check_duplicate_type_names` 게이트 자체의 적색 재현은
`tests/unit/scripts/test_check_audit_regressions.py`에 있다.
"""

from __future__ import annotations

import statistics
import time
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any
from uuid import uuid4

import pytest
from pydantic import ValidationError

from src.api.contracts.idempotency import IdempotencyScope
from src.data.models.base import AssetClass
from src.data.models.trading import OrderSide, OrderType
from src.services.oms.contracts.v1_commands import OrderIdempotencyScope, SubmitOrderCommand
from src.services.oms.domain.idempotency import (
    build_scope,
    client_order_id,
    command_digest,
    scope_hash,
)

_ALNUM = "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"


def _api_scope_kwargs(**overrides: Any) -> dict[str, Any]:
    defaults: dict[str, Any] = dict(
        header_key="k" * 20,
        route="orders.submit",
        tenant_id=uuid4(),
        subject_id=uuid4(),
        digest="0" * 64,
    )
    defaults.update(overrides)
    return defaults


def _order_scope_kwargs(**overrides: Any) -> dict[str, Any]:
    defaults: dict[str, Any] = dict(
        tenant_id=uuid4(),
        account_ref="acct-1",
        provider="bitget",
        strategy_id="s1",
        strategy_version="1.0.0",
        execution_id=1,
        intent_seq=1,
        window_start=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )
    defaults.update(overrides)
    return defaults


def _command_kwargs(scope: Any) -> dict[str, Any]:
    return dict(
        command_id=uuid4(),
        trace_id=uuid4(),
        scope=scope,
        symbol="BTC/USDT",
        side=OrderSide.BUY,
        order_type=OrderType.MARKET,
        quantity=Decimal("0.01"),
        price=None,
        asset_class=AssetClass.CRYPTO,
        actor_subject_id=uuid4(),
        issued_at=datetime.now(timezone.utc),
    )


# ---- negative(≥3) — 개명이 고치는 실제 위험: 서로의 kwargs를 받아들이지 않는다 ----


def test_order_idempotency_scope_rejects_api_idempotency_scope_kwargs() -> None:
    """OMS 멱등 스코프는 API 헤더 스코프의 필드(header_key/route/digest 등)로는
    만들어지지 않는다 — 개명 전이라면 이름이 같아 정적 타입검사가 놓쳤을
    조합이다."""
    with pytest.raises(ValidationError):
        OrderIdempotencyScope(**_api_scope_kwargs())


def test_api_idempotency_scope_rejects_order_idempotency_scope_kwargs() -> None:
    """역방향도 마찬가지 — API 헤더 스코프는 OMS 스코프의 필드(account_ref/
    provider/strategy_id 등)로는 만들어지지 않는다."""
    with pytest.raises(ValidationError):
        IdempotencyScope(**_order_scope_kwargs())


def test_submit_order_command_rejects_api_scope_instance() -> None:
    """`SubmitOrderCommand.scope`는 `OrderIdempotencyScope` 타입으로 고정돼
    있다 — 같은 이름이던 시절이라면 조용히 통과했을 `IdempotencyScope`
    인스턴스를 지금은 pydantic이 model_type 오류로 거부한다."""
    wrong_scope = IdempotencyScope(**_api_scope_kwargs())
    with pytest.raises(ValidationError):
        SubmitOrderCommand(**_command_kwargs(wrong_scope))


# ---- failure-injection: 직렬화 경계를 넘어 잘못 배달돼도 fail-closed ----


def test_submit_order_command_fails_closed_on_cross_context_dict_payload() -> None:
    """실패 주입: 메시지 큐/로그 재생 등 직렬화 경계 너머에서 API 헤더
    스코프가 dict로 떠돌다 OMS 명령의 `scope`로 잘못 배달되는 상황을
    흉내 낸다. pydantic이 부족한 필드(account_ref/provider/strategy_id/
    strategy_version/execution_id/intent_seq/window_start)를 구체적으로
    나열하며 거부해야 한다 — 조용히 기본값을 채우거나 부분 스코프로
    넘어가면 안 된다(fail-closed)."""
    api_scope = IdempotencyScope(**_api_scope_kwargs())
    cross_context_payload = api_scope.model_dump(mode="json")

    with pytest.raises(ValidationError) as exc_info:
        SubmitOrderCommand(**_command_kwargs(cross_context_payload))

    missing_fields = {
        str(err["loc"][-1]) for err in exc_info.value.errors() if err["type"] == "missing"
    }
    assert {
        "account_ref",
        "provider",
        "strategy_id",
        "strategy_version",
        "execution_id",
        "intent_seq",
        "window_start",
    } <= missing_fields


# ---- 성능 단언 ----

_PERF_ITERATIONS = 1000
# ADR-2026-09-09-C Decision 1 예산표에는 순수 계산(DB/네트워크 없음) 전용
# 항목이 없다 — 가장 가까운 유사 항목("사전거래 게이트 p99 5ms", 동일하게
# 단일 요청 내 순수 계산)을 자체 예산으로 차용한다(task-3160/3162/3164/
# 3168/3169/3173/3174/3177 DEEPEN과 동일 차용 근거). 실측 로컬 p99는
# ~0.02ms로 250배 이상 여유가 있다 — 이 예산은 퇴화(예: 정렬/해시 알고리즘을
# 이차 이상으로 바꾸는 회귀)를 잡기 위한 것이지 현재 성능을 빡빡하게 좇는
# 것이 아니다.
_PERF_BUDGET_P99_MS = 5.0


def test_scope_hash_and_client_order_id_and_command_digest_meet_borrowed_p99_budget() -> None:
    """순수 계산 경로(scope_hash -> client_order_id -> command_digest) p99가
    차용 예산(5ms) 내에 있어야 한다 — sha256/정렬 알고리즘이 실수로
    이차 이상으로 퇴화하는 회귀를 잡는다."""
    scopes = [
        build_scope(
            tenant_id=uuid4(),
            account_ref="acct-1",
            provider="bitget",
            strategy_id="s1",
            strategy_version="1.0.0",
            execution_id=i,
            intent_seq=i,
            window_start=datetime.now(timezone.utc),
        )
        for i in range(_PERF_ITERATIONS)
    ]
    commands = [SubmitOrderCommand(**_command_kwargs(scope)) for scope in scopes]

    latencies_ms: list[float] = []
    for scope, command in zip(scopes, commands, strict=True):
        started = time.perf_counter()
        scope_hash(scope)
        client_order_id(scope, max_len=40, charset=_ALNUM)
        command_digest(command)
        latencies_ms.append((time.perf_counter() - started) * 1000.0)

    latencies_ms.sort()
    p99_ms = latencies_ms[int(len(latencies_ms) * 0.99)]
    print(
        f"\nOrderIdempotencyScope pure-compute path (n={_PERF_ITERATIONS}): "
        f"p50={statistics.median(latencies_ms):.4f}ms p99={p99_ms:.4f}ms "
        f"(차용 예산 p99<{_PERF_BUDGET_P99_MS}ms)"
    )
    assert p99_ms < _PERF_BUDGET_P99_MS, (
        f"scope_hash/client_order_id/command_digest p99({p99_ms:.4f}ms)가 "
        f"차용 예산({_PERF_BUDGET_P99_MS}ms)을 넘었습니다."
    )
