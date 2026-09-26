"""RD-5 `application/link_entities.py` -- DEEPEN(task-2906, DEPTH_DC_RD
소급감사 task-2726) D1 -> D2 증빙.

기존 test_link_entities.py는 fake 포트로 미매핑 보존 1건과 2회 재실행
멱등성 1건(D1)을 증명했다 -- 소급감사에서 "D2 하한 미달: 실패주입(DB/
네트워크/크래시) 없음, 수치 성능 단언 없음, 게이트 적색 재현 없음"으로
지적됐다. 이 파일이 그 부족분을 채운다. link_entities.py는 무수정 -- 새
기능 없음, 깊이만 올림.

RD-4가 실제 Postgres 어댑터를 아직 소유하지 않으므로(entity_link_repository
는 순수 포트만 존재), DB/네트워크 실패는 fake 포트 수준에서 주입한다 --
실 커넥션 장애든 fake의 예외든 `link_entities` 입장에서는 똑같이 "포트
호출이 예외로 끝난다"는 신호이고, 이를 삼키지 않고 전파하는지가 fail-closed
계약이다.

1. 실패 주입/크래시 -- `list_unlinked`(조회, 네트워크 단절을 흉내),
   `save_links`(쓰기, DB 장애를 흉내), `resolver.resolve`(배치 중간 크래시)
   각각이 예외를 던지면 `link_entities`가 이를 삼키지 않고 그대로
   전파하는지, 그리고 배치 계산 중 크래시가 나면 `save_links`가 아예
   호출되지 않아(부분 저장 없음) 이미 계산된 일부 결과가 새어나가지
   않는지를 증명한다.
2. 성능 단언 -- 기본 `limit=500` 규모 배치를 절대시간 예산 내에 처리하는지,
   그리고 resolver 호출 횟수가 항목 수에 선형(O(n), 항목당 정확히 1회
   이하)인지를 수치로 단언한다.
3. 게이트 적색 재현 -- 동일 항목을 3단계로 재생한다: 최초 실행(미해결,
   포트 docstring대로 풀에 남음) -> 재실행(resolver가 아직 못 찾음, 여전히
   남음) -> resolver가 뒤늦게 해결(symbol_master 카탈로그 갱신을 흉내)한
   뒤 3차 실행(해결되어 풀에서 빠짐). 각 단계의 상태가 다음 단계로
   새지 않음을 증명한다 -- `list_unlinked`가 실패-남음 항목을 계속
   돌려주는 계약이 깨지면 이 테스트가 결정적으로 실패한다.
"""

from __future__ import annotations

import time
from datetime import datetime, timezone
from uuid import UUID, uuid4

import pytest

from src.foundation.research_data.application.link_entities import link_entities
from src.foundation.research_data.contracts.v1 import ResearchItem
from src.foundation.research_data.domain.entity_link import EntityKey, EntityLinkResult

_NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)
_TENANT = uuid4()


def _item(*, instruments: tuple[str, ...] = ()) -> ResearchItem:
    return ResearchItem(
        item_id=uuid4(),
        source_id="OPENDART",
        kind="filing",
        published_at=_NOW,
        known_at=_NOW,
        instruments=instruments,
        title="title",
        body_ref=None,
        url="https://example.invalid/x",
        language="ko",
        hash="deadbeef",
        revision_of=None,
    )


class _FakeResolver:
    def __init__(self, mapping: dict[str, str]) -> None:
        self._mapping = mapping
        self.call_count = 0

    def resolve(self, key: EntityKey) -> str | None:
        self.call_count += 1
        return self._mapping.get(key.value)


class _CrashingResolver:
    """세 번째 호출에서 크래시한다 (배치 중간 장애를 흉내)."""

    def __init__(self) -> None:
        self.call_count = 0

    def resolve(self, key: EntityKey) -> str | None:
        self.call_count += 1
        if self.call_count == 3:
            raise RuntimeError("injected resolver crash mid-batch")
        return None


class _FakeEntityLinkRepository:
    def __init__(self, items: list[ResearchItem]) -> None:
        self._pool: dict[UUID, ResearchItem] = {item.item_id: item for item in items}
        self.save_calls: list[list[EntityLinkResult]] = []
        self.list_unlinked_calls = 0

    async def list_unlinked(
        self, conn: object, *, tenant_id: UUID, limit: int
    ) -> list[ResearchItem]:
        self.list_unlinked_calls += 1
        return list(self._pool.values())[:limit]

    async def save_links(self, conn: object, results: list[EntityLinkResult]) -> None:
        self.save_calls.append(list(results))
        for result in results:
            if result.instrument_id is not None:
                self._pool.pop(result.item_id, None)


class _BoomOnListUnlinked(_FakeEntityLinkRepository):
    async def list_unlinked(
        self, conn: object, *, tenant_id: UUID, limit: int
    ) -> list[ResearchItem]:
        raise ConnectionError("injected network failure on list_unlinked")


class _BoomOnSaveLinks(_FakeEntityLinkRepository):
    async def save_links(self, conn: object, results: list[EntityLinkResult]) -> None:
        raise RuntimeError("injected DB failure on save_links")


# ---- 실패 주입/크래시 ----


async def test_link_entities_propagates_list_unlinked_network_failure() -> None:
    repo = _BoomOnListUnlinked([_item(instruments=("005930",))])
    resolver = _FakeResolver({"005930": "INSTR-1"})

    with pytest.raises(ConnectionError, match="injected network failure"):
        await link_entities(None, repo, resolver, tenant_id=_TENANT, limit=10)

    assert repo.save_calls == [], "no save should happen if the read itself failed"


async def test_link_entities_propagates_save_links_db_failure() -> None:
    item = _item(instruments=("005930",))
    repo = _BoomOnSaveLinks([item])
    resolver = _FakeResolver({"005930": "INSTR-1"})

    with pytest.raises(RuntimeError, match="injected DB failure"):
        await link_entities(None, repo, resolver, tenant_id=_TENANT, limit=10)


async def test_link_entities_resolver_crash_mid_batch_never_calls_save_links() -> None:
    """세 번째 항목에서 resolver가 크래시하면, 이미 계산된 앞 두 건의
    결과라도 `save_links`가 아예 호출되지 않는다 -- 전부 계산이 끝나기
    전까지는 아무것도 저장하지 않는 것이 부분 저장(일부만 링크된 상태로
    남는 것)보다 안전하다."""
    items = [_item(instruments=(f"00593{i}",)) for i in range(5)]
    repo = _FakeEntityLinkRepository(items)
    resolver = _CrashingResolver()

    with pytest.raises(RuntimeError, match="injected resolver crash mid-batch"):
        await link_entities(None, repo, resolver, tenant_id=_TENANT, limit=10)

    assert repo.save_calls == []
    assert resolver.call_count == 3


# ---- 성능 단언 ----


@pytest.mark.perf
async def test_link_entities_default_limit_batch_meets_latency_budget() -> None:
    """기본 `limit=500` 규모(OpenDART 일일 배치 상한)를 절대시간 예산 내에
    처리해야 한다."""
    budget_sec = 1.0  # 실측 로컬 <0.05s(fake 포트, I/O 없음), CI 편차 감안
    items = [_item(instruments=(f"{i:06d}",)) for i in range(500)]
    mapping = {f"{i:06d}": f"INSTR-{i}" for i in range(0, 500, 2)}  # 절반만 매핑
    repo = _FakeEntityLinkRepository(items)
    resolver = _FakeResolver(mapping)

    start = time.perf_counter()
    results = await link_entities(None, repo, resolver, tenant_id=_TENANT, limit=500)
    elapsed = time.perf_counter() - start

    print(f"[RD-5 link_entities] 500건 배치 {elapsed:.3f}s (budget<{budget_sec}s)")
    assert len(results) == 500
    assert elapsed < budget_sec, (
        f"500건 배치 처리가 예산({budget_sec}s)을 넘었습니다({elapsed:.3f}s)."
    )
    assert resolver.call_count == 500, (
        "each item has exactly one deterministic key -> one resolve call"
    )


# ---- 게이트 적색 재현 (미해결 -> 여전히 미해결 -> 뒤늦게 해결) ----


async def test_link_entities_replays_unresolved_then_still_unresolved_then_resolved() -> None:
    """포트 docstring 계약: "NOT_FOUND로 남은 항목은 다음 `list_unlinked`
    에서도 계속 나온다." 이 계약이 깨지면(예: 미매핑 항목을 실수로 풀에서
    제거) 2단계에서 결과가 비어버려 이 테스트가 결정적으로 실패한다."""
    item = _item(instruments=("005930",))
    repo = _FakeEntityLinkRepository([item])
    resolver = _FakeResolver({})  # 아직 심볼 마스터에 없음

    # 1단계: 최초 실행 -- 미해결.
    first = await link_entities(None, repo, resolver, tenant_id=_TENANT, limit=10)
    assert len(first) == 1
    assert first[0].instrument_id is None
    assert item.item_id in repo._pool  # 실패 항목은 풀에서 빠지지 않는다

    # 2단계: 재실행 -- resolver가 여전히 못 찾음, 여전히 풀에 남아 재시도된다.
    second = await link_entities(None, repo, resolver, tenant_id=_TENANT, limit=10)
    assert len(second) == 1
    assert second[0].instrument_id is None
    assert item.item_id in repo._pool

    # 3단계: symbol_master 카탈로그가 갱신됐다고 가정 -- resolver가 이제 해결.
    resolver._mapping["005930"] = "INSTR-1"
    third = await link_entities(None, repo, resolver, tenant_id=_TENANT, limit=10)
    assert len(third) == 1
    assert third[0].instrument_id == "INSTR-1"
    assert item.item_id not in repo._pool  # 해결된 뒤에야 풀에서 빠진다

    assert repo.save_calls == [first, second, third]
    assert all(r[0].item_id == item.item_id for r in (first, second, third))
