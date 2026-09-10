"""DC-27 `domain/entitlement/source_contract.py` — DEEPEN(task-2887,
docs/audit/DEPTH_DC_RD.md#1764) D1 -> D3 증빙.

기존 test_source_contract.py는 negative 8건으로 EXPIRED/NOT_YET_VALID/
NOT_FOUND/naive-datetime/credential_ref 유출을 단건씩 증명했다(D1) —
소급감사(task-2726)에서 "실패주입 없음, 성능단언 없음, 게이트적색 재현
없음, D3 요소 없음"으로 지적됐다(1764행). 이 파일이 그 부족분을 채운다.
`source_contract.py`는 순수 함수/모델이라 DB/네트워크가 없으므로, 이
파일에서 "실패주입"이란 pydantic 계약(AwareDatetime·enum·0폭 유효기간)이
조용한 통과를 막는지와 `permits_use`가 명시적으로 허가하지 않은 모든
(scope, use) 조합을 전수로 거부하는지를, "게이트 적색 재현"이란 같은
source_id의 계약 하나를 시간축으로 재생하며(미등록 -> 발효 전 -> 유효 ->
만료) 각 경계에서 정확한 사유로만 갈라지는지를, "D3"란 순수 함수에 공유
가변 상태가 없어 다중 스레드 동시 호출도 서로 오염되지 않는지를 뜻한다.
새 기능 없음, 깊이만 올림.

1. 실패 주입 — naive valid_from/valid_to, 0폭 유효기간(valid_to==valid_from),
   미지의 tier/redistribution_scope 문자열이 구성 시점에 거부되는지, 그리고
   `permits_use`가 명시 허가 테이블에 없는 모든 (scope, use) 조합을 전수로
   거부하는지를 증명한다.
2. 성능 단언 — 대량 반복 판정과 대용량 capability 구성이 절대시간 예산
   내에 있음을 증명한다.
3. 게이트 적색 재현 — 동일 source_id의 계약 하나를 시간축으로 재생한다:
   미등록(NOT_FOUND) -> 발효 전(NOT_YET_VALID) -> 유효 구간(allowed) ->
   valid_to 경계(EXPIRED). 각 전이에서 이전 단계의 사유가 다음 단계로 새지
   않음을 증명한다.
4. 동시 다중 인스턴스(D3) — 스레드풀로 서로 다른 contract/as_of 조합을
   동시에 `authorize_source()`에 넣어도 결과가 섞이지 않음을 증명한다.
"""

from __future__ import annotations

import itertools
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from src.foundation.market_data.domain.entitlement.source_contract import (
    DataUse,
    RedistributionScope,
    SourceCapability,
    SourceContract,
    SourceContractDenialReason,
    SourceContractTier,
    authorize_source,
    permits_use,
)

_NOW = datetime(2026, 9, 7, 0, 0, tzinfo=timezone.utc)

_EXPLICITLY_PERMITTED: frozenset[tuple[RedistributionScope, DataUse]] = frozenset(
    {
        (RedistributionScope.USER_SCOPED, DataUse.USER_OWN_DISPLAY),
        (RedistributionScope.INTERNAL, DataUse.INTERNAL_CALC),
        (RedistributionScope.DISPLAY, DataUse.INTERNAL_CALC),
        (RedistributionScope.DISPLAY, DataUse.USER_OWN_DISPLAY),
        (RedistributionScope.DISPLAY, DataUse.SHARED_DISPLAY),
        (RedistributionScope.REDISTRIBUTE, DataUse.INTERNAL_CALC),
        (RedistributionScope.REDISTRIBUTE, DataUse.USER_OWN_DISPLAY),
        (RedistributionScope.REDISTRIBUTE, DataUse.SHARED_DISPLAY),
        (RedistributionScope.REDISTRIBUTE, DataUse.EXPORT_OR_RESELL),
    }
)


def _contract(
    *,
    source_id: str = "OPENDART",
    tier: SourceContractTier = SourceContractTier.PERSONAL,
    credential_ref: str = "vault:opendart:v1",
    redistribution_scope: RedistributionScope = RedistributionScope.INTERNAL,
    rate_limit: int = 100,
    quota: int = 1000,
    valid_from: datetime = _NOW - timedelta(days=30),
    valid_to: datetime | None = None,
    capability: SourceCapability | None = None,
) -> SourceContract:
    return SourceContract(
        source_id=source_id,
        tier=tier,
        credential_ref=credential_ref,
        redistribution_scope=redistribution_scope,
        rate_limit=rate_limit,
        quota=quota,
        valid_from=valid_from,
        valid_to=valid_to,
        capability=capability
        or SourceCapability(
            asset_classes=frozenset({"EQUITY_KR"}),
            resolutions=frozenset({"1d"}),
            corporate_actions=False,
        ),
    )


# ---- 실패 주입 ----


def test_naive_valid_from_is_rejected_at_construction() -> None:
    """`valid_from`도 `AwareDatetime`이다 — naive 값이 조용히 UTC로 취급되지
    않고 계약 구성 시점에 즉시 거부돼야 한다."""
    with pytest.raises(ValidationError):
        _contract(valid_from=datetime(2026, 8, 1))


def test_naive_valid_to_is_rejected_at_construction() -> None:
    with pytest.raises(ValidationError):
        _contract(valid_to=datetime(2026, 10, 1))


def test_zero_width_valid_range_rejected_at_construction() -> None:
    """negative 경계: `valid_to == valid_from`(0폭 유효기간)도 `<` 케이스와
    동일하게 구성 시점에 거부돼야 한다 — 배타적 상한 규칙이 등호 경계에서도
    새지 않음을 증명한다."""
    with pytest.raises(ValidationError):
        _contract(valid_from=_NOW, valid_to=_NOW)


@pytest.mark.parametrize(
    "build",
    [
        lambda: SourceContract(
            source_id="OPENDART",
            tier="NOT_A_TIER",
            credential_ref="vault:x:v1",
            redistribution_scope=RedistributionScope.INTERNAL,
            rate_limit=1,
            quota=1,
            valid_from=_NOW - timedelta(days=1),
            valid_to=None,
            capability=SourceCapability(
                asset_classes=frozenset({"EQUITY_KR"}), resolutions=frozenset({"1d"})
            ),
        ),
        lambda: SourceContract(
            source_id="OPENDART",
            tier=SourceContractTier.PERSONAL,
            credential_ref="vault:x:v1",
            redistribution_scope="NOT_A_SCOPE",
            rate_limit=1,
            quota=1,
            valid_from=_NOW - timedelta(days=1),
            valid_to=None,
            capability=SourceCapability(
                asset_classes=frozenset({"EQUITY_KR"}), resolutions=frozenset({"1d"})
            ),
        ),
    ],
    ids=["tier", "redistribution_scope"],
)
def test_unknown_enum_value_in_contract_is_rejected_fail_closed(build) -> None:
    """오타·미지의 tier/redistribution_scope 문자열이 계약 행에 실려도 조용히
    통과해 판정 시점에 원인 모를 결과로 새는 대신, 구성 시점에 즉시
    `ValidationError`로 거부돼야 한다(저장소 계층이 DB CHECK 제약을 우회하는
    손상된 행을 반환하는 방어적 상황을 흉내)."""
    with pytest.raises(ValidationError):
        build()


def test_permits_use_fail_closed_for_every_undeclared_combination() -> None:
    """`permits_use`는 명시적으로 허가 테이블에 올린 (scope, use) 조합
    9종을 제외한 나머지 전수(20종 중 11종)를 전부 거부해야 한다 — 새 scope나
    새 use가 추가될 때 허가 테이블 갱신을 잊으면 이 테스트가 즉시 실패한다
    (fail-closed 기본값이 실제로 전수 적용됨을 증명, D2)."""
    all_combinations = set(itertools.product(RedistributionScope, DataUse))
    assert _EXPLICITLY_PERMITTED <= all_combinations

    for scope, use in all_combinations:
        expected = (scope, use) in _EXPLICITLY_PERMITTED
        assert permits_use(scope, use) is expected, (
            f"{scope}/{use} 조합의 permits_use 결과가 명시 허가 테이블과 어긋난다."
        )

    # NONE은 단 하나도 허가하지 않는다 — 미지정은 항상 거부(D2 "fail-closed").
    assert all(not permits_use(RedistributionScope.NONE, use) for use in DataUse)
    # EXPORT_OR_RESELL은 REDISTRIBUTE 외 어떤 scope로도 새지 않는다.
    assert all(
        not permits_use(scope, DataUse.EXPORT_OR_RESELL)
        for scope in RedistributionScope
        if scope is not RedistributionScope.REDISTRIBUTE
    )


# ---- 성능 단언 ----


@pytest.mark.perf
def test_authorize_source_repeated_calls_meet_throughput_budget() -> None:
    """동일 계약에 대한 반복 판정(요청마다 저장소에서 새로 읽어온 DTO를
    받는 상황을 흉내)이 처리량 예산을 지켜야 한다."""
    iterations = 100_000
    budget_sec = 5.0  # 실측 로컬 <0.5s, CI 환경 편차 감안
    contract = _contract(valid_from=_NOW - timedelta(days=30), valid_to=None)

    start = time.perf_counter()
    for i in range(iterations):
        result = authorize_source(contract, _NOW + timedelta(seconds=i % 3600))
        assert result.allowed is True
    elapsed = time.perf_counter() - start

    print(
        f"[DC-27 source_contract] authorize_source() x{iterations} in {elapsed:.3f}s "
        f"(budget<{budget_sec}s)"
    )
    assert elapsed < budget_sec, (
        f"authorize_source {iterations}회 반복이 예산({budget_sec}s)을 넘었습니다({elapsed:.3f}s)."
    )


@pytest.mark.perf
def test_large_capability_construction_meets_latency_budget() -> None:
    """자산군/해상도 문자열이 대량(각 5,000개)인 capability를 가진 계약
    구성이 절대시간 예산 내여야 한다(pydantic frozenset 검증이 이차로
    퇴화하지 않았는지 — 다자산 통합 소스가 늘어날수록 이 값도 커진다)."""
    n = 5_000
    budget_sec = 2.0  # 실측 로컬 <0.2s
    asset_classes = frozenset(f"ASSET_{i}" for i in range(n))
    resolutions = frozenset(f"RES_{i}" for i in range(n))

    start = time.perf_counter()
    contract = _contract(
        capability=SourceCapability(
            asset_classes=asset_classes, resolutions=resolutions, corporate_actions=True
        )
    )
    grant = authorize_source(contract, _NOW)
    elapsed = time.perf_counter() - start

    print(
        f"[DC-27 source_contract] capability({n}x2) construction+authorize in {elapsed:.4f}s "
        f"(budget<{budget_sec}s)"
    )
    assert grant.allowed is True
    assert grant.capability is not None
    assert len(grant.capability.asset_classes) == n
    assert elapsed < budget_sec, (
        f"대용량 capability 구성이 예산({budget_sec}s)을 넘었습니다({elapsed:.4f}s)."
    )


# ---- 게이트 적색 재현 — 동일 source_id 계약을 시간축으로 재생 ----


def test_gate_red_lifecycle_replayed_over_time_never_leaks_wrong_reason() -> None:
    """같은 source_id 계약 하나(승격 전 저장소 조회를 흉내)를 시간축 4단계로
    재생한다 — 미등록 -> 발효 전 -> 유효 -> 만료. 각 단계에서 이전 단계의
    사유가 다음 단계로 새지 않고 정확히 그 단계의 사유로만 갈라짐을
    증명한다(§9 DC-27 DoD "valid_to 경과 계약은 fail-closed 거부")."""
    valid_from = _NOW - timedelta(days=10)
    valid_to = _NOW + timedelta(days=10)
    contract = _contract(
        source_id="ECOS", tier=SourceContractTier.BUSINESS, valid_from=valid_from, valid_to=valid_to
    )

    # 0단계: 저장소가 행을 못 찾음(신규/오타 source_id) -> NOT_FOUND.
    stage0 = authorize_source(None, _NOW)
    assert stage0.allowed is False
    assert stage0.denial_reason == SourceContractDenialReason.NOT_FOUND

    # 1단계: 행은 있으나 as_of가 valid_from 이전(계약 등록만 되고 미발효) -> NOT_YET_VALID.
    stage1 = authorize_source(contract, valid_from - timedelta(seconds=1))
    assert stage1.allowed is False
    assert stage1.denial_reason == SourceContractDenialReason.NOT_YET_VALID
    assert stage1.tier is None

    # 2단계: as_of가 유효 구간 한가운데 -> 완전허용, 계약 필드가 그대로 전달됨.
    stage2 = authorize_source(contract, _NOW)
    assert stage2.allowed is True
    assert stage2.denial_reason is None
    assert stage2.tier == SourceContractTier.BUSINESS
    assert stage2.redistribution_scope == RedistributionScope.INTERNAL

    # 3단계: as_of가 valid_to 경계(배타적 상한) -> EXPIRED
    # (1단계의 NOT_YET_VALID나 2단계의 tier 값이 여기로 새면 안 됨).
    stage3 = authorize_source(contract, valid_to)
    assert stage3.allowed is False
    assert stage3.denial_reason == SourceContractDenialReason.EXPIRED
    assert stage3.tier is None

    # 4단계: valid_to를 한참 지난 시점도 여전히 EXPIRED로 고정(재발효 없음).
    stage4 = authorize_source(contract, valid_to + timedelta(days=365))
    assert stage4.allowed is False
    assert stage4.denial_reason == SourceContractDenialReason.EXPIRED


# ---- 동시 다중 인스턴스(D3) ----


def test_concurrent_authorize_source_calls_across_threads_do_not_cross_contaminate() -> None:
    """서로 다른 결과(거부 3종 + 완전허용)를 내야 하는 4개 조합을 스레드풀에서
    반복 동시 호출한다 — 각 워커가 매번 자기 입력에 맞는 결과만 받고, 다른
    워커의 결과로 오염되지 않아야 한다(순수 함수라면 공유 가변 상태가 없어
    당연해야 하나, 회귀 시 모듈 레벨 캐시가 실수로 추가되는 것을 이
    테스트가 잡는다)."""
    active_contract = _contract(
        source_id="A", tier=SourceContractTier.ENTERPRISE, valid_from=_NOW - timedelta(days=1)
    )
    not_yet_contract = _contract(source_id="B", valid_from=_NOW + timedelta(days=1))
    expired_contract = _contract(
        source_id="C", valid_from=_NOW - timedelta(days=30), valid_to=_NOW - timedelta(days=1)
    )

    scenarios: dict[str, tuple] = {
        "not_found": (
            None,
            _NOW,
            {"allowed": False, "reason": SourceContractDenialReason.NOT_FOUND},
        ),
        "not_yet_valid": (
            not_yet_contract,
            _NOW,
            {"allowed": False, "reason": SourceContractDenialReason.NOT_YET_VALID},
        ),
        "expired": (
            expired_contract,
            _NOW,
            {"allowed": False, "reason": SourceContractDenialReason.EXPIRED},
        ),
        "allowed": (
            active_contract,
            _NOW,
            {"allowed": True, "tier": SourceContractTier.ENTERPRISE},
        ),
    }

    def _run(name: str, repeat: int) -> tuple[str, list[bool]]:
        contract, as_of, expected = scenarios[name]
        outcomes = []
        for _ in range(repeat):
            result = authorize_source(contract, as_of)
            ok = result.allowed == expected["allowed"]
            if expected["allowed"]:
                ok = ok and result.tier == expected["tier"]
            else:
                ok = ok and result.denial_reason == expected["reason"]
            outcomes.append(ok)
        return name, outcomes

    names = list(scenarios) * 30  # 4종 x 30 = 120개 동시 작업, 서로 인터리빙되도록
    with ThreadPoolExecutor(max_workers=12) as pool:
        futures = [pool.submit(_run, name, 25) for name in names]
        results = [f.result() for f in futures]

    for name, outcomes in results:
        assert all(outcomes), f"동시 실행 중 시나리오 {name!r}가 다른 워커에 오염됐다."
