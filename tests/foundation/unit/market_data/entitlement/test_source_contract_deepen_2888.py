"""DC-28 `domain/entitlement/source_contract.py` — DEEPEN(task-2888,
docs/audit/DEPTH_DC_RD.md#1765) D1 -> D3 증빙.

`test_source_contract.py`(DC-27, task-1764)는 등급 승격·만료·`credential_ref`
비유출·`permits_use` 매트릭스 9개 조합을 이미 증명했다(D1). 소급감사(task-2726)
는 DC-28(1765, "재배포 스코프 강제")의 실제 강제 지점 커밋(a6f8e5b)을 보고
"성능단언 없음, 게이트적색 재현 없음"으로 지적했다. 이 파일이 그 부족분을
`permits_use`/`authorize_source` 순수 함수 수준에서 채운다(I/O가 있는 async
게이트 `authorize_redistribution` 자체의 증빙은
`test_read_api_deepen_2888.py`가 맡는다).

1. 실패 주입 — 오타 스코프 문자열은 계약 구성 시점에 `ValidationError`로
   거부되고, 등록되지 않은 용도 문자열은 (raise가 아니라) 가장 넓은
   스코프에서도 fail-closed로 거부(`False`)됨을 증명한다.
2. 성능 단언 — 대량 반복 판정이 절대시간 예산 내에 있음을 증명한다.
3. 게이트 적색 재현 — 세 강제 지점(백테스트=INTERNAL_CALC, 차트=SHARED_DISPLAY,
   내보내기=EXPORT_OR_RESELL)과 D7 지점(USER_OWN_DISPLAY)이 요구하는 정확한
   문턱 스코프를 스코프×용도 전수 매트릭스(5x4=20)로 재생해, 어느 한 칸이라도
   매트릭스가 잘못 넓어지면(예: INTERNAL이 SHARED_DISPLAY까지 허용) 이 테스트가
   적색이 됨을 보장한다.
4. 동시 다중 인스턴스(D3) — 스레드풀로 서로 다른 스코프/용도 조합을 동시에
   `permits_use()`에 넣어도 결과가 섞이지 않음을 증명한다.
"""

from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from typing import cast

import pytest
from pydantic import ValidationError

from src.foundation.market_data.domain.entitlement.source_contract import (
    DataUse,
    RedistributionScope,
    SourceCapability,
    SourceContract,
    SourceContractTier,
    authorize_source,
    permits_use,
)

_NOW = datetime(2026, 9, 10, tzinfo=timezone.utc)


def _contract(
    *,
    scope: RedistributionScope = RedistributionScope.INTERNAL,
    valid_from: datetime = _NOW - timedelta(days=30),
    valid_to: datetime | None = None,
) -> SourceContract:
    return SourceContract(
        source_id="BITGET",
        tier=SourceContractTier.ENTERPRISE,
        credential_ref="vault:bitget:v1",
        redistribution_scope=scope,
        rate_limit=1000,
        quota=1_000_000,
        valid_from=valid_from,
        valid_to=valid_to,
        capability=SourceCapability(
            asset_classes=frozenset({"CRYPTO"}), resolutions=frozenset({"1m"})
        ),
    )


# ---- 실패 주입 ----


def test_unknown_redistribution_scope_string_is_rejected_at_construction() -> None:
    """오타 스코프 문자열이 계약에 실려도 조용히 통과해 나중에 판정
    시점에 원인 모를 결과(엄격 비교 실패로 항상 거부되거나, 최악의 경우
    잘못 캐스팅돼 통과)로 새는 대신, 구성 시점에 즉시 거부돼야 한다."""
    with pytest.raises(ValidationError):
        SourceContract(
            source_id="BITGET",
            tier=SourceContractTier.ENTERPRISE,
            credential_ref="vault:bitget:v1",
            redistribution_scope=cast(RedistributionScope, "NOT_A_SCOPE"),
            rate_limit=1000,
            quota=1_000_000,
            valid_from=_NOW - timedelta(days=30),
            valid_to=None,
            capability=SourceCapability(
                asset_classes=frozenset({"CRYPTO"}), resolutions=frozenset({"1m"})
            ),
        )


def test_permits_use_fail_closed_denies_unknown_data_use_value_even_for_widest_scope() -> None:
    """`permits_use`는 `use in _PERMITTED_USES[scope]`(frozenset 멤버십)로만
    판정한다 — 미지의 `DataUse` 문자열이 들어와도(예: 새 용도 추가 시 이
    문자열을 매트릭스에 등록하는 걸 잊음) `KeyError`로 죽지 않고 조용히
    거부(`False`)로 fail-closed 된다. 가장 넓은 스코프(REDISTRIBUTE)에도
    등록되지 않은 용도는 절대 허용으로 새지 않음을 증명한다."""
    assert permits_use(RedistributionScope.REDISTRIBUTE, cast(DataUse, "NOT_A_USE")) is False


# ---- 성능 단언 ----


@pytest.mark.perf
def test_permits_use_repeated_calls_meet_throughput_budget() -> None:
    """`permits_use`는 요청마다(캔들 페이지네이션 등) 반복 호출될 수 있는
    hot path다 — 딕셔너리 멤버십 조회가 반복 호출에서 예산을 지켜야 한다."""
    iterations = 100_000
    budget_sec = 2.0  # 실측 로컬 <0.05s, CI 편차 감안
    scopes = list(RedistributionScope)
    uses = list(DataUse)

    start = time.perf_counter()
    for i in range(iterations):
        permits_use(scopes[i % len(scopes)], uses[i % len(uses)])
    elapsed = time.perf_counter() - start

    print(
        f"[DC-28 source_contract] permits_use() x{iterations} in {elapsed:.4f}s "
        f"(budget<{budget_sec}s)"
    )
    assert elapsed < budget_sec, (
        f"permits_use {iterations}회 반복이 예산({budget_sec}s)을 넘었습니다({elapsed:.4f}s)."
    )


@pytest.mark.perf
def test_authorize_source_with_many_calls_meets_latency_budget() -> None:
    """`authorize_source`도 요청마다 재호출되는 순수 판정이다 — 절대시간
    예산 내에 있어야 실시간 판정 경로가 다건 요청에서도 지연 SLA를
    지킨다."""
    n = 20_000
    budget_sec = 2.0  # 실측 로컬 <0.1s
    contract = _contract()

    start = time.perf_counter()
    for _ in range(n):
        grant = authorize_source(contract, _NOW)
        assert grant.allowed is True
    elapsed = time.perf_counter() - start

    print(
        f"[DC-28 source_contract] authorize_source() x{n} in {elapsed:.4f}s (budget<{budget_sec}s)"
    )
    assert elapsed < budget_sec, (
        f"authorize_source {n}회 반복이 예산({budget_sec}s)을 넘었습니다({elapsed:.4f}s)."
    )


# ---- 게이트 적색 재현 — 스코프×용도 전수 매트릭스 ----


def test_gate_red_full_scope_use_matrix_never_leaks_beyond_declared_grid() -> None:
    """세 강제 지점(백테스트=INTERNAL_CALC, 차트=SHARED_DISPLAY, 내보내기=
    EXPORT_OR_RESELL) + D7 지점(USER_OWN_DISPLAY)을 스코프 5종 전체에 재생한
    전수 매트릭스(5x4=20칸)다. 매트릭스의 어느 한 칸이라도 넓어지면(가장
    치명적인 회귀: INTERNAL 계약 소스가 SHARED_DISPLAY/차트에 새는 것,
    §9 DC-28 DoD 원문 시나리오) 이 테스트가 즉시 적색이 된다. 참(True)인
    칸이 딱 9개뿐이라는 것도 함께 고정해, 새 용도/스코프 추가 시 실수로
    과다 허용되는 조합이 늘어나면 개수 단언에서도 걸린다."""
    expected = {
        (RedistributionScope.NONE, DataUse.INTERNAL_CALC): False,
        (RedistributionScope.NONE, DataUse.USER_OWN_DISPLAY): False,
        (RedistributionScope.NONE, DataUse.SHARED_DISPLAY): False,
        (RedistributionScope.NONE, DataUse.EXPORT_OR_RESELL): False,
        (RedistributionScope.USER_SCOPED, DataUse.INTERNAL_CALC): False,
        (RedistributionScope.USER_SCOPED, DataUse.USER_OWN_DISPLAY): True,
        (RedistributionScope.USER_SCOPED, DataUse.SHARED_DISPLAY): False,
        (RedistributionScope.USER_SCOPED, DataUse.EXPORT_OR_RESELL): False,
        (RedistributionScope.INTERNAL, DataUse.INTERNAL_CALC): True,
        (RedistributionScope.INTERNAL, DataUse.USER_OWN_DISPLAY): False,
        (RedistributionScope.INTERNAL, DataUse.SHARED_DISPLAY): False,
        (RedistributionScope.INTERNAL, DataUse.EXPORT_OR_RESELL): False,
        (RedistributionScope.DISPLAY, DataUse.INTERNAL_CALC): True,
        (RedistributionScope.DISPLAY, DataUse.USER_OWN_DISPLAY): True,
        (RedistributionScope.DISPLAY, DataUse.SHARED_DISPLAY): True,
        (RedistributionScope.DISPLAY, DataUse.EXPORT_OR_RESELL): False,
        (RedistributionScope.REDISTRIBUTE, DataUse.INTERNAL_CALC): True,
        (RedistributionScope.REDISTRIBUTE, DataUse.USER_OWN_DISPLAY): True,
        (RedistributionScope.REDISTRIBUTE, DataUse.SHARED_DISPLAY): True,
        (RedistributionScope.REDISTRIBUTE, DataUse.EXPORT_OR_RESELL): True,
    }
    assert len(expected) == len(RedistributionScope) * len(DataUse) == 20

    actual = {(scope, use): permits_use(scope, use) for scope, use in expected}

    assert actual == expected
    assert sum(actual.values()) == 9  # 참인 칸 개수 고정 — 과다 허용 회귀 감지.
    # DoD 원문 시나리오를 이름으로도 명시: INTERNAL 계약은 차트(SHARED_DISPLAY)에 안 새고,
    # 백테스트(INTERNAL_CALC)에는 여전히 쓸 수 있다.
    assert permits_use(RedistributionScope.INTERNAL, DataUse.SHARED_DISPLAY) is False
    assert permits_use(RedistributionScope.INTERNAL, DataUse.INTERNAL_CALC) is True


# ---- 동시 다중 인스턴스(D3) ----


def test_concurrent_permits_use_calls_across_threads_do_not_cross_contaminate() -> None:
    """서로 다른 결과를 내야 하는 스코프×용도 조합을 스레드풀에서 반복
    동시 호출한다 — 순수 함수이므로 당연히 섞이지 않아야 하나, 회귀 시
    모듈 레벨 캐시 등이 실수로 추가되는 것을 이 테스트가 잡는다."""
    scenarios = {
        "internal_denies_chart": (
            RedistributionScope.INTERNAL,
            DataUse.SHARED_DISPLAY,
            False,
        ),
        "internal_permits_backtest": (
            RedistributionScope.INTERNAL,
            DataUse.INTERNAL_CALC,
            True,
        ),
        "user_scoped_permits_own_display": (
            RedistributionScope.USER_SCOPED,
            DataUse.USER_OWN_DISPLAY,
            True,
        ),
        "user_scoped_denies_shared_display": (
            RedistributionScope.USER_SCOPED,
            DataUse.SHARED_DISPLAY,
            False,
        ),
        "display_permits_chart": (
            RedistributionScope.DISPLAY,
            DataUse.SHARED_DISPLAY,
            True,
        ),
        "redistribute_permits_export": (
            RedistributionScope.REDISTRIBUTE,
            DataUse.EXPORT_OR_RESELL,
            True,
        ),
        "display_denies_export": (
            RedistributionScope.DISPLAY,
            DataUse.EXPORT_OR_RESELL,
            False,
        ),
    }

    def _run(name: str, repeat: int) -> tuple[str, list[bool]]:
        scope, use, expected = scenarios[name]
        return name, [permits_use(scope, use) == expected for _ in range(repeat)]

    names = list(scenarios) * 20  # 7종 x 20 = 140개 동시 작업.
    with ThreadPoolExecutor(max_workers=14) as pool:
        futures = [pool.submit(_run, name, 25) for name in names]
        results = [f.result() for f in futures]

    for name, outcomes in results:
        assert all(outcomes), f"동시 실행 중 시나리오 {name!r}가 다른 워커에 오염됐다."
