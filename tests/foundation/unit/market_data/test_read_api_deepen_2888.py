"""DC-28 `application/read_api.py::authorize_redistribution` — DEEPEN
(task-2888, docs/audit/DEPTH_DC_RD.md#1765) D1 -> D3 증빙.

`authorize_redistribution()`은 세 강제 지점(읽기 API·차트, 백테스트, 내보내기)
이 공유하는 단일 게이트다(`read_api.py` 모듈 docstring) — 지금까지 이 함수
자체를 겨냥한 단위 테스트는 없었고, `market_data.py`/`backtests.py` 라우터
통합테스트가 간접적으로만 exercise했다. 이 파일은 실 DB 없이 페이크
`SourceContractRepository`로 이 게이트 자체를 직접 겨냥해 소급감사가 지적한
부족분(성능단언·게이트적색 재현·D3)을 채운다.

1. 실패 주입 — 계약 조회 자체가 실패(DB 오류)하면 조용히 허용으로 접히지
   않고 예외가 그대로 전파됨을, 계약이 없으면(D2 "미지정은 NONE") 거부됨을
   증명한다.
2. 성능 단언 — 게이트 통과(허용)·차단(거부) 양쪽 다 반복 호출이 절대시간
   예산 내에 있음을 증명한다.
3. 게이트 적색 재현 — (a) INTERNAL 계약 소스가 차트(SHARED_DISPLAY)엔
   막히고 백테스트(INTERNAL_CALC)엔 열리는, §9 DC-28 DoD 원문 시나리오를
   실제 게이트 함수로 재생한다. (b) `permits_use()` 체크를 생략한 가상의
   회귀 경로를 나란히 구성해, 그 경로였다면 실제로 새는 것과 실제 게이트가
   막는 것을 대비시켜 이 체크가 무엇을 방어하는지 재현한다.
4. 동시 다중 인스턴스(D3) — `asyncio.gather`로 서로 다른 소스/스코프/용도
   조합을 동시에 게이트에 넣어도(여러 요청 핸들러 인스턴스가 같은 프로세스
   안에서 동시에 이 게이트를 호출하는 실제 배치를 흉내) 결과가 섞이지
   않음을 증명한다.
"""

from __future__ import annotations

import asyncio
import time
from datetime import datetime, timedelta, timezone

import pytest

from src.foundation.market_data.application.read_api import (
    MarketDataNotFoundError,
    authorize_redistribution,
)
from src.foundation.market_data.domain.entitlement.source_contract import (
    DataUse,
    RedistributionScope,
    SourceCapability,
    SourceContract,
    SourceContractTier,
    permits_use,
)

_NOW = datetime(2026, 9, 10, tzinfo=timezone.utc)


def _contract(
    *, source_id: str = "BITGET", scope: RedistributionScope = RedistributionScope.DISPLAY
) -> SourceContract:
    return SourceContract(
        source_id=source_id,
        tier=SourceContractTier.ENTERPRISE,
        credential_ref="vault:bitget:v1",
        redistribution_scope=scope,
        rate_limit=1000,
        quota=1_000_000,
        valid_from=_NOW - timedelta(days=30),
        valid_to=None,
        capability=SourceCapability(
            asset_classes=frozenset({"CRYPTO"}), resolutions=frozenset({"1m"})
        ),
    )


class _FakeSourceContractRepository:
    """`source_id -> SourceContract | None` 룩업만 하는 인메모리 페이크
    (test_market_data_router.py의 동명 페이크와 같은 패턴, 실DB 없음)."""

    def __init__(self, contracts: dict[str, SourceContract | None]) -> None:
        self._contracts = contracts
        self.calls: list[str] = []

    async def get(self, conn: object, source_id: str) -> SourceContract | None:
        self.calls.append(source_id)
        return self._contracts.get(source_id)


class _RaisingRepository:
    async def get(self, conn: object, source_id: str) -> SourceContract | None:
        raise ConnectionError("source_contract 조회 중 DB 연결 실패(시뮬레이션)")


def _clock() -> datetime:
    return _NOW


# ---- 실패 주입 ----


async def test_repo_failure_propagates_instead_of_silently_allowing() -> None:
    """계약 조회 자체가 실패하면(DB 장애 등) 예외가 그대로 위로 전파돼야
    한다 — 실패를 삼켜 "계약 없음 -> 거부"와 같은 경로로 접어 조용히
    허용/거부 어느 쪽으로도 위장하면 안 된다(진짜 원인이 관측에서 사라짐)."""
    with pytest.raises(ConnectionError):
        await authorize_redistribution(
            None, "BITGET", repo=_RaisingRepository(), clock=_clock, use=DataUse.SHARED_DISPLAY
        )


async def test_missing_contract_row_denies_as_none_scope() -> None:
    """D2 "미지정은 NONE으로 취급" — `source_contract` 행 자체가 없으면
    (등록 안 된 소스) `MarketDataNotFoundError`로 거부된다."""
    repo = _FakeSourceContractRepository({})
    with pytest.raises(MarketDataNotFoundError):
        await authorize_redistribution(
            None, "UNKNOWN_SOURCE", repo=repo, clock=_clock, use=DataUse.INTERNAL_CALC
        )


# ---- 성능 단언 ----


@pytest.mark.perf
async def test_repeated_gate_calls_meet_latency_budget_allow_and_deny() -> None:
    """허용 경로(DISPLAY+SHARED_DISPLAY)와 거부 경로(NONE+SHARED_DISPLAY)
    양쪽 다 반복 호출이 절대시간 예산 내에 있어야 한다 — 페이지네이션된
    캔들 조회마다 이 게이트가 매번 재호출되므로, 여기서 선형 이상의 비용이
    생기면 차트 API 지연 SLA 전체에 영향을 준다."""
    iterations = 2_000
    budget_sec = 3.0  # 실측 로컬 <0.2s, 이벤트루프 스케줄링 오버헤드 감안
    allow_repo = _FakeSourceContractRepository({"BITGET": _contract()})
    deny_repo = _FakeSourceContractRepository({"BITGET": _contract(scope=RedistributionScope.NONE)})

    start = time.perf_counter()
    for _ in range(iterations):
        await authorize_redistribution(
            None, "BITGET", repo=allow_repo, clock=_clock, use=DataUse.SHARED_DISPLAY
        )
        with pytest.raises(MarketDataNotFoundError):
            await authorize_redistribution(
                None, "BITGET", repo=deny_repo, clock=_clock, use=DataUse.SHARED_DISPLAY
            )
    elapsed = time.perf_counter() - start

    print(
        f"[DC-28 read_api] authorize_redistribution() x{iterations * 2}(allow+deny) in "
        f"{elapsed:.4f}s (budget<{budget_sec}s)"
    )
    assert elapsed < budget_sec, (
        f"게이트 {iterations * 2}회 반복이 예산({budget_sec}s)을 넘었습니다({elapsed:.4f}s)."
    )


# ---- 게이트 적색 재현 ----


async def test_gate_red_internal_denies_chart_but_permits_backtest() -> None:
    """§9 DC-28 DoD 원문 시나리오를 실제 게이트로 재생한다 — INTERNAL 계약
    소스는 차트(SHARED_DISPLAY)엔 `MarketDataNotFoundError`로 거부되고,
    같은 계약·같은 소스가 백테스트(INTERNAL_CALC)엔 그대로 열려야 한다.
    두 판정이 같은 계약 행을 공유하므로, 이 대비가 깨지면(둘 다 허용되거나
    둘 다 거부되면) 재배포 스코프가 강제되지 않고 있다는 뜻이다."""
    repo = _FakeSourceContractRepository({"BITGET": _contract(scope=RedistributionScope.INTERNAL)})

    with pytest.raises(MarketDataNotFoundError):
        await authorize_redistribution(
            None, "BITGET", repo=repo, clock=_clock, use=DataUse.SHARED_DISPLAY
        )
    await authorize_redistribution(  # 예외 없이 통과해야 한다.
        None, "BITGET", repo=repo, clock=_clock, use=DataUse.INTERNAL_CALC
    )


async def test_gate_red_without_permits_use_check_would_leak_internal_to_chart() -> None:
    """`authorize_redistribution()`이 실제로 무엇을 막고 있는지를, 그 체크가
    빠진 가상의 회귀 경로와 나란히 대비해 재현한다: 계약 유효성만 보고
    `permits_use()`를 생략하는 나이브한 구현이었다면, INTERNAL 계약도
    `grant.allowed`만으로 SHARED_DISPLAY(차트)까지 통과시켰을 것이다. 실제
    게이트는 그 추가 체크 덕분에 거부한다 — 이 테스트가 적색이 된다는 것은
    `permits_use()` 호출이 통째로 삭제되거나 무력화됐다는 뜻이다."""
    contract = _contract(scope=RedistributionScope.INTERNAL)
    repo = _FakeSourceContractRepository({"BITGET": contract})

    # 나이브한(계약 유효성만 보는) 회귀 경로를 흉내: 유효한 계약이면 무조건 통과.
    naive_would_allow = contract is not None  # authorize_source_access의 grant.allowed와 동치
    assert naive_would_allow is True
    assert permits_use(RedistributionScope.INTERNAL, DataUse.SHARED_DISPLAY) is False

    # 실제 게이트는 permits_use() 덕분에 거부한다.
    with pytest.raises(MarketDataNotFoundError):
        await authorize_redistribution(
            None, "BITGET", repo=repo, clock=_clock, use=DataUse.SHARED_DISPLAY
        )


# ---- 동시 다중 인스턴스(D3) ----


async def test_concurrent_gate_calls_across_sources_do_not_cross_contaminate() -> None:
    """`asyncio.gather`로 여러 소스·스코프·용도 조합을 동시에 게이트에
    넣는다(실제 서버 프로세스에서 여러 요청이 이벤트루프 안에서 인터리빙
    되는 배치를 흉내). 공유 딕셔너리 조회일 뿐인 페이크 repo라도, 게이트
    함수 자체가 결과를 뒤섞지 않고 각 호출자에게 자기 입력에 맞는 결과만
    돌려줘야 한다."""
    repo = _FakeSourceContractRepository(
        {
            "NONE_SRC": _contract(source_id="NONE_SRC", scope=RedistributionScope.NONE),
            "INTERNAL_SRC": _contract(source_id="INTERNAL_SRC", scope=RedistributionScope.INTERNAL),
            "DISPLAY_SRC": _contract(source_id="DISPLAY_SRC", scope=RedistributionScope.DISPLAY),
            "REDIST_SRC": _contract(source_id="REDIST_SRC", scope=RedistributionScope.REDISTRIBUTE),
        }
    )

    async def _expect_allow(source_id: str, use: DataUse) -> bool:
        await authorize_redistribution(None, source_id, repo=repo, clock=_clock, use=use)
        return True

    async def _expect_deny(source_id: str, use: DataUse) -> bool:
        try:
            await authorize_redistribution(None, source_id, repo=repo, clock=_clock, use=use)
        except MarketDataNotFoundError:
            return True
        return False

    tasks = []
    for _ in range(30):
        tasks.append(_expect_deny("NONE_SRC", DataUse.INTERNAL_CALC))
        tasks.append(_expect_deny("INTERNAL_SRC", DataUse.SHARED_DISPLAY))
        tasks.append(_expect_allow("INTERNAL_SRC", DataUse.INTERNAL_CALC))
        tasks.append(_expect_allow("DISPLAY_SRC", DataUse.SHARED_DISPLAY))
        tasks.append(_expect_deny("DISPLAY_SRC", DataUse.EXPORT_OR_RESELL))
        tasks.append(_expect_allow("REDIST_SRC", DataUse.EXPORT_OR_RESELL))

    results = await asyncio.gather(*tasks)

    assert all(results), "동시 실행 중 게이트 결과가 다른 호출자와 뒤섞였다."
    assert len(repo.calls) == 30 * 6
