"""L4-27b — `submit_order.py` §7.2 계측 실배선 증명(task-3506).

Spec: docs/specs/L4_execution_oms_and_exchange_v1.0.md §7.2, §9 L4-27.
QA 3496 결론: `submit_order.py`에 `MetricsPort` 호출이 0건이었다(이연분 미회수).
이 파일은 실제 배선(`OMS_ORDER_SUBMIT_COUNT_TOTAL{outcome,venue}`/
`OMS_ORDER_SUBMIT_DURATION_SECONDS{venue}`)이 네 가지 `outcome`
(accepted/denied/replay/error) 각각에서 정확히 1회 기록됨을 실DB로 증명한다.
이름 자체의 등재/정규식 회귀는 `tests/unit/oms/test_metrics_names.py`가 이미
커버한다(이 리프 이전부터 `OMS_ORDER_SUBMIT_*` 상수가 등재돼 있었다 — 여기서는
그 이름을 실제로 호출하는지만 증명).
"""

from __future__ import annotations

from dataclasses import dataclass, field

import pytest

from src.core.observability.metric_names import (
    OMS_ORDER_SUBMIT_COUNT_TOTAL,
    OMS_ORDER_SUBMIT_DURATION_SECONDS,
)
from src.foundation.entities.adapters.postgres_repository import PostgresEntityRepository
from src.services.oms.application.submit_order import OrderSubmitDeniedError, submit_order
from src.services.oms.domain.errors import UnknownSymbolError
from tests.integration.oms.conftest import create_test_tenant, seed_entity_context
from tests.performance.oms._fixtures import (
    create_running_execution,
    submit_command,
    submit_profile,
    submit_registry,
)
from tests.support.oms_outbox_fakes import allow_gate, deny_gate


@dataclass
class _SpyMetrics:
    counters: list[tuple[str, dict[str, str] | None]] = field(default_factory=list)
    observations: list[tuple[str, float, dict[str, str] | None]] = field(default_factory=list)

    def counter(self, name: str, labels: dict[str, str] | None = None) -> None:
        self.counters.append((name, labels))

    def observe(self, name: str, value: float, labels: dict[str, str] | None = None) -> None:
        self.observations.append((name, value, labels))

    def gauge(self, name: str, value: float, labels: dict[str, str] | None = None) -> None:
        return None


async def _setup(pool):
    user_id = await create_test_tenant(pool)
    execution_id = await create_running_execution(pool, user_id)
    entity_context = await seed_entity_context(pool, user_id)
    entity_repo = PostgresEntityRepository(pool)
    return user_id, execution_id, entity_context, entity_repo


# ---- accepted -----------------------------------------------------------------------


async def test_submit_order_accepted_records_counter_and_duration_once(pool) -> None:
    user_id, execution_id, entity_context, entity_repo = await _setup(pool)
    spy = _SpyMetrics()

    result = await submit_order(
        submit_command(user_id, execution_id, 1),
        pool=pool,
        profile=submit_profile(),
        registry=submit_registry(),
        pre_submit_gate=allow_gate,
        entity_context=entity_context,
        entity_repo=entity_repo,
        metrics=spy,
    )

    assert result.status.value == "VALIDATED"
    assert spy.counters == [
        (OMS_ORDER_SUBMIT_COUNT_TOTAL, {"outcome": "accepted", "venue": "bitget"})
    ]
    assert len(spy.observations) == 1
    name, value, labels = spy.observations[0]
    assert name == OMS_ORDER_SUBMIT_DURATION_SECONDS
    assert value >= 0.0
    assert labels == {"venue": "bitget"}


# ---- denied (negative 1) -------------------------------------------------------------


async def test_submit_order_denied_records_denied_outcome(pool) -> None:
    user_id, execution_id, entity_context, entity_repo = await _setup(pool)
    spy = _SpyMetrics()

    with pytest.raises(OrderSubmitDeniedError):
        await submit_order(
            submit_command(user_id, execution_id, 1),
            pool=pool,
            profile=submit_profile(),
            registry=submit_registry(),
            pre_submit_gate=deny_gate,
            entity_context=entity_context,
            entity_repo=entity_repo,
            metrics=spy,
        )

    assert spy.counters == [
        (OMS_ORDER_SUBMIT_COUNT_TOTAL, {"outcome": "denied", "venue": "bitget"})
    ]
    assert len(spy.observations) == 1
    assert spy.observations[0][2] == {"venue": "bitget"}


# ---- replay (negative 2) ---------------------------------------------------------------


async def test_submit_order_replay_records_replay_outcome(pool) -> None:
    """같은 scope 재시도는 `orders.client_order_id` UNIQUE 충돌(collided) 경로로
    해소된다 — 두 번째 호출의 outcome은 "replay"다(모듈 docstring — EXISTING
    claim 분기는 정상 도달 불가, 실제 replay는 collided 경로가 담당)."""
    user_id, execution_id, entity_context, entity_repo = await _setup(pool)
    cmd = submit_command(user_id, execution_id, 1)
    first_spy = _SpyMetrics()
    await submit_order(
        cmd,
        pool=pool,
        profile=submit_profile(),
        registry=submit_registry(),
        pre_submit_gate=allow_gate,
        entity_context=entity_context,
        entity_repo=entity_repo,
        metrics=first_spy,
    )
    replay_spy = _SpyMetrics()

    second = await submit_order(
        cmd,
        pool=pool,
        profile=submit_profile(),
        registry=submit_registry(),
        pre_submit_gate=allow_gate,
        entity_context=entity_context,
        entity_repo=entity_repo,
        metrics=replay_spy,
    )

    assert second.status.value == "VALIDATED"
    assert replay_spy.counters == [
        (OMS_ORDER_SUBMIT_COUNT_TOTAL, {"outcome": "replay", "venue": "bitget"})
    ]
    assert len(replay_spy.observations) == 1
    assert replay_spy.observations[0][2] == {"venue": "bitget"}


# ---- error (negative 3) ----------------------------------------------------------------


async def test_submit_order_error_outcome_on_unknown_symbol(pool) -> None:
    """레지스트리 미등록 심볼(`UnknownSymbolError`)처럼 gate에 닿기 전 실패도
    "error"로 기록된다 — venue는 profile에서 여전히 확정 가능하다."""
    user_id, execution_id, entity_context, entity_repo = await _setup(pool)
    cmd = submit_command(user_id, execution_id, 1).model_copy(update={"symbol": "ETH/USDT"})
    spy = _SpyMetrics()

    with pytest.raises(UnknownSymbolError):
        await submit_order(
            cmd,
            pool=pool,
            profile=submit_profile(),
            registry=submit_registry(),
            pre_submit_gate=allow_gate,
            entity_context=entity_context,
            entity_repo=entity_repo,
            metrics=spy,
        )

    assert spy.counters == [(OMS_ORDER_SUBMIT_COUNT_TOTAL, {"outcome": "error", "venue": "bitget"})]
    assert len(spy.observations) == 1
    assert spy.observations[0][2] == {"venue": "bitget"}


# ---- 라벨 카디널리티 상한 + PII/redaction ------------------------------------------


async def test_submit_order_outcome_label_cardinality_matches_declared_set(pool) -> None:
    """`outcome` 라벨은 4값({accepted,denied,replay,error})을 절대 넘지 않는다 —
    네 경로를 모두 실행해 관측된 집합이 정확히 그 4개와 같은지 단언한다(그
    이상 값이 새 코드 경로로 생기면 이 테스트가 잡는다)."""
    user_id, execution_id, entity_context, entity_repo = await _setup(pool)
    seen: set[str] = set()

    accepted_spy = _SpyMetrics()
    await submit_order(
        submit_command(user_id, execution_id, 1),
        pool=pool,
        profile=submit_profile(),
        registry=submit_registry(),
        pre_submit_gate=allow_gate,
        entity_context=entity_context,
        entity_repo=entity_repo,
        metrics=accepted_spy,
    )
    seen.update(labels["outcome"] for _, labels in accepted_spy.counters if labels)

    denied_spy = _SpyMetrics()
    with pytest.raises(OrderSubmitDeniedError):
        await submit_order(
            submit_command(user_id, execution_id, 2),
            pool=pool,
            profile=submit_profile(),
            registry=submit_registry(),
            pre_submit_gate=deny_gate,
            entity_context=entity_context,
            entity_repo=entity_repo,
            metrics=denied_spy,
        )
    seen.update(labels["outcome"] for _, labels in denied_spy.counters if labels)

    replay_cmd = submit_command(user_id, execution_id, 3)
    await submit_order(
        replay_cmd,
        pool=pool,
        profile=submit_profile(),
        registry=submit_registry(),
        pre_submit_gate=allow_gate,
        entity_context=entity_context,
        entity_repo=entity_repo,
    )
    replay_spy = _SpyMetrics()
    await submit_order(
        replay_cmd,
        pool=pool,
        profile=submit_profile(),
        registry=submit_registry(),
        pre_submit_gate=allow_gate,
        entity_context=entity_context,
        entity_repo=entity_repo,
        metrics=replay_spy,
    )
    seen.update(labels["outcome"] for _, labels in replay_spy.counters if labels)

    error_spy = _SpyMetrics()
    error_cmd = submit_command(user_id, execution_id, 4).model_copy(update={"symbol": "ETH/USDT"})
    with pytest.raises(UnknownSymbolError):
        await submit_order(
            error_cmd,
            pool=pool,
            profile=submit_profile(),
            registry=submit_registry(),
            pre_submit_gate=allow_gate,
            entity_context=entity_context,
            entity_repo=entity_repo,
            metrics=error_spy,
        )
    seen.update(labels["outcome"] for _, labels in error_spy.counters if labels)

    assert seen == {"accepted", "denied", "replay", "error"}


async def test_submit_order_metric_labels_carry_no_pii_or_order_identifiers(pool) -> None:
    """§7.3 — 라벨 키는 `{outcome, venue}`뿐이어야 한다. order_id/tenant_id/
    client_order_id/symbol 같은 고카디널리티·PII성 필드가 라벨로 새면 이
    테스트가 실패한다(카디널리티 폭발 + 개인정보 노출 이중 방지)."""
    user_id, execution_id, entity_context, entity_repo = await _setup(pool)
    spy = _SpyMetrics()

    await submit_order(
        submit_command(user_id, execution_id, 1),
        pool=pool,
        profile=submit_profile(),
        registry=submit_registry(),
        pre_submit_gate=allow_gate,
        entity_context=entity_context,
        entity_repo=entity_repo,
        metrics=spy,
    )

    for _, labels in spy.counters:
        assert labels is not None
        assert set(labels.keys()) == {"outcome", "venue"}
    for _, _, labels in spy.observations:
        assert labels is not None
        assert set(labels.keys()) == {"venue"}
        forbidden = {"order_id", "tenant_id", "client_order_id", "symbol", "user_id"}
        assert forbidden.isdisjoint(labels.keys())
