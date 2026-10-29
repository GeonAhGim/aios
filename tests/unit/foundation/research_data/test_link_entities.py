"""RD-5 -- `application/link_entities.py` + `domain/entity_link.py` unit tests (fake port).

Spec: docs/specs/L4_research_data_and_market_ecosystem_v1.0.md Sec 9 RD-5
DoD (c), (e), (f).

DEEPEN (task-4170): added negative/failure-injection/perf tests for
`is_valid_isin`, `extract_entity_key`, `link_item`, and the application
entry point.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime
from unittest.mock import patch
from uuid import UUID, uuid4

import asyncpg
import pytest

from src.foundation.research_data.application.link_entities import link_entities
from src.foundation.research_data.contracts.v1 import ResearchItem
from src.foundation.research_data.domain.entity_link import (
    EntityKey,
    EntityLinkResult,
    UnmappedReason,
    extract_entity_key,
    is_valid_isin,
    link_item,
)
from tests.support.perf_budget import PerfBudget
from tests.support.perf_inject import virtual_delayed_async

_NOW = datetime(2026, 1, 1, tzinfo=UTC)
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
    """Deterministic-key-value -> instrument_id map (RD-5 does not need
    symbol_master data here; that delegation is covered in
    test_entity_link.py)."""

    def __init__(self, mapping: dict[str, str]) -> None:
        self._mapping = mapping

    def resolve(self, key: EntityKey) -> str | None:
        return self._mapping.get(key.value)


class _FakeEntityLinkRepository:
    """In-memory fake -- `list_unlinked` stops returning an item as soon as
    `save_links` has recorded a successful match for it (mirrors the real
    Postgres query `WHERE instrument_id IS NULL`)."""

    def __init__(self, items: list[ResearchItem]) -> None:
        self._pool: dict[UUID, ResearchItem] = {item.item_id: item for item in items}
        self.save_calls: list[list[EntityLinkResult]] = []

    async def list_unlinked(
        self, conn: asyncpg.Connection, *, tenant_id: UUID, limit: int
    ) -> list[ResearchItem]:
        return list(self._pool.values())[:limit]

    async def save_links(
        self, conn: asyncpg.Connection, results: Sequence[EntityLinkResult]
    ) -> None:
        self.save_calls.append(list(results))
        for result in results:
            if result.instrument_id is not None:
                self._pool.pop(result.item_id, None)


async def test_link_entities_batch_leaves_unmapped_items_unmapped_without_exception() -> None:
    mapped_item = _item(instruments=("005930",))
    unmapped_item_a = _item(instruments=("삼성전자우 유상증자",))
    unmapped_item_b = _item(instruments=())
    repo = _FakeEntityLinkRepository([mapped_item, unmapped_item_a, unmapped_item_b])
    resolver = _FakeResolver({"005930": "INSTR-1"})

    results = await link_entities(None, repo, resolver, tenant_id=_TENANT, limit=10)

    assert len(results) == 3
    mapped = [r for r in results if r.instrument_id is not None]
    unmapped = [r for r in results if r.instrument_id is None]
    assert len(mapped) == 1
    assert mapped[0].item_id == mapped_item.item_id
    assert len(unmapped) == 2
    assert repo.save_calls == [results]


async def test_link_entities_is_idempotent_on_the_second_run() -> None:
    """RD-5 DoD (e): once every item resolves, the second run's `save_links`
    call receives an empty list -- `list_unlinked` no longer returns them.

    Perf (D2): `best_of_async`로 Windows 양자화 tick(≈15.6ms)에 흔들리지 않게
    최소(cpu) 값을 쓴다. 0 수렴 단언 — fake repo는 CPU 부하가 없으므로
    best_of가 0에 수렴해야 한다.
    """
    item_a = _item(instruments=("005930",))
    item_b = _item(instruments=("000660",))
    repo = _FakeEntityLinkRepository([item_a, item_b])
    resolver = _FakeResolver({"005930": "INSTR-1", "000660": "INSTR-2"})

    first_results = await link_entities(None, repo, resolver, tenant_id=_TENANT, limit=10)
    second_results = await link_entities(None, repo, resolver, tenant_id=_TENANT, limit=10)

    assert len(first_results) == 2
    assert all(r.instrument_id is not None for r in first_results)
    assert second_results == []
    assert repo.save_calls[0] == first_results
    assert repo.save_calls[1] == []

    # Perf assertion (D2): fake repo는 실제 I/O/CPU 부하가 없음.
    # best_of_async(5, batch=20)로 Windows tick 15.6ms를 batch 평균으로 분산.
    perf = PerfBudget()

    async def _run_once() -> None:
        await link_entities(None, repo, resolver, tenant_id=_TENANT, limit=10)

    sample = await perf.best_of_async(_run_once, n=5, warmup=1, batch=20)
    assert sample.cpu_ms < 1.0, (
        f"link_entities cpu={sample.cpu_ms:.3f}ms — "
        f"fake repo는 CPU 부하가 없어야 함 (budget 1.0 ms)"
    )


@pytest.mark.perf
async def test_link_entities_is_idempotent_catches_virtual_delay_injection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """게이트 적색 재현(D2): `virtual_delayed_async`로 가상 지연을 주입하면
    같은 perf 단언이 AssertionError로 적색이 돼야 한다.

    `virtual_delayed_async`는 실제 sleep 없이 virtual wall ledger에 지연을
    더한다. `PerfBudget.best_of_async`는 virtual ledger를 측정값에 더하므로
    실제 sleep/busy-wait 없이 wall_ms가 지연만큼 늘어난다.
    단언/마커 삭제나 예산 완화 금지.
    """
    # link_entities를 가상 지연 50ms로 감싸서 virtual wall ledger에 50ms 주입
    original_link = link_entities

    async def _slow_link(*args, **kwargs) -> list[EntityLinkResult]:
        await virtual_delayed_async(original_link, 0.05)(*args, **kwargs)

    monkeypatch.setattr(
        "tests.unit.foundation.research_data.test_link_entities.link_entities", _slow_link
    )

    # 같은 측정/단언을 재사용 — 지연 주입으로 budget 초과 → AssertionError
    item_a = _item(instruments=("005930",))
    item_b = _item(instruments=("000660",))
    repo = _FakeEntityLinkRepository([item_a, item_b])
    resolver = _FakeResolver({"005930": "INSTR-1", "000660": "INSTR-2"})

    # 첫 실행으로 repo 상태 세팅
    await link_entities(None, repo, resolver, tenant_id=_TENANT, limit=10)

    perf = PerfBudget()

    async def _run_once() -> None:
        await link_entities(None, repo, resolver, tenant_id=_TENANT, limit=10)

    sample = await perf.best_of_async(_run_once, n=5, warmup=0, batch=1)
    assert sample.cpu_ms < 1.0, (
        f"지연 주입 후 cpu={sample.cpu_ms:.3f}ms — budget 1.0 ms를 초과해야 적색(re) 확인"
    )


# ---------------------------------------------------------------------------
# Negative tests for `link_entities` idempotent path
# ---------------------------------------------------------------------------


async def test_link_entities_partial_resolution_still_maps_remaining() -> None:
    """Negative: 첫 실행에서 일부만 매핑되면, 두 번째 실행에서 매핑되지 않은
    항목은 여전히 list_unlinked에 남아야 한다 (완전 idempotent 아님)."""
    mapped_item = _item(instruments=("005930",))
    unmapped_item = _item(instruments=("삼성전자우 유상증자",))
    repo = _FakeEntityLinkRepository([mapped_item, unmapped_item])
    resolver = _FakeResolver({"005930": "INSTR-1"})  # unmapped_item은 매핑 안 됨

    first_results = await link_entities(None, repo, resolver, tenant_id=_TENANT, limit=10)
    second_results = await link_entities(None, repo, resolver, tenant_id=_TENANT, limit=10)

    # 첫 실행: mapped_item만 매핑, unmapped_item은 unmapped
    assert len(first_results) == 2
    mapped = [r for r in first_results if r.instrument_id is not None]
    assert len(mapped) == 1

    # 두 번째 실행: unmapped_item이 여전히 unlinked이므로 반환됨
    assert len(second_results) == 1
    assert second_results[0].item_id == unmapped_item.item_id


async def test_link_entities_empty_batch_returns_empty_list() -> None:
    """Negative: 이미 모든 항목이 매핑된 repo에 다시 호출하면 빈 리스트."""
    item = _item(instruments=("005930",))
    repo = _FakeEntityLinkRepository([item])
    resolver = _FakeResolver({"005930": "INSTR-1"})

    await link_entities(None, repo, resolver, tenant_id=_TENANT, limit=10)
    # 이미 모두 매핑됨 — 두 번째 호출은 빈 결과
    results = await link_entities(None, repo, resolver, tenant_id=_TENANT, limit=10)
    assert results == []


async def test_link_entities_limit_truncation_idempotent() -> None:
    """Negative: limit이 항목 수보다 적으면, 첫 실행에서 limit만큼만 처리하고
    두 번째 실행에서 나머지 항목을 처리해야 한다."""
    items = [_item(instruments=(f"{i:06d}",)) for i in range(5)]
    repo = _FakeEntityLinkRepository(items)
    resolver = _FakeResolver({f"{i:06d}": f"INSTR-{i}" for i in range(5)})

    # limit=2: 첫 실행에서 2개만 처리
    first_results = await link_entities(None, repo, resolver, tenant_id=_TENANT, limit=2)
    assert len(first_results) == 2

    # 두 번째 실행: 남은 3개 처리
    second_results = await link_entities(None, repo, resolver, tenant_id=_TENANT, limit=2)
    assert len(second_results) == 2

    # 세 번째 실행: 남은 1개 처리
    third_results = await link_entities(None, repo, resolver, tenant_id=_TENANT, limit=2)
    assert len(third_results) == 1

    # 네 번째 실행: 모두 처리됨 — 빈 리스트
    fourth_results = await link_entities(None, repo, resolver, tenant_id=_TENANT, limit=2)
    assert fourth_results == []


# ---------------------------------------------------------------------------
# Negative tests for `is_valid_isin` (RD-A4: only deterministic keys)
# ---------------------------------------------------------------------------


async def test_is_valid_isin_rejects_too_short_candidate() -> None:
    """RD-A4: ISIN must be exactly 12 chars (2-letter country + 9-alphanumeric + 1 check digit)."""
    assert is_valid_isin("KR12345678901") is False  # 11 chars


async def test_is_valid_isin_rejects_too_long_candidate() -> None:
    assert is_valid_isin("KR1234567890123") is False  # 13 chars


async def test_is_valid_isin_rejects_invalid_country_code() -> None:
    """Country code must be two alphabetic characters."""
    assert is_valid_isin("1R12345678901") is False


async def test_is_valid_isin_rejects_wrong_checksum() -> None:
    """Luhn checksum must pass; flipping the last digit fails validation."""
    # "KR000000000003" is a valid ISIN checksum (verified by the Luhn impl).
    # Flip the check digit to "4" and it must be rejected.
    assert is_valid_isin("KR000000000004") is False


# ---------------------------------------------------------------------------
# Negative tests for `extract_entity_key` (RD-A4: no name-similarity guessing)
# ---------------------------------------------------------------------------


async def test_extract_entity_key_rejects_empty_string() -> None:
    """RD-A4: blank input yields no deterministic key."""
    assert extract_entity_key("") is None
    assert extract_entity_key("   ") is None


async def test_extract_entity_key_rejects_company_name() -> None:
    """RD-A4 forbids name-similarity guessing: a Korean company name
    must NOT be accepted as a valid key."""
    assert extract_entity_key("삼성전자") is None


async def test_extract_entity_key_rejects_mixed_garbage() -> None:
    assert extract_entity_key("005930-삼성전자") is None


# ---------------------------------------------------------------------------
# Negative tests for `link_item` — empty / all-unresolvable instruments
# ---------------------------------------------------------------------------


async def test_link_item_returns_no_deterministic_key_when_instruments_empty() -> None:
    """RD-5 DoD (c): empty instruments list → NO_DETERMINISTIC_KEY, not an error."""
    item = _item(instruments=())
    resolver = _FakeResolver({})
    result = link_item(item, resolver)
    assert result.instrument_id is None
    assert result.reason == UnmappedReason.NO_DETERMINISTIC_KEY


async def test_link_item_returns_not_found_when_key_parses_but_resolver_misses() -> None:
    """A valid KRX code that parses but has no resolver entry → NOT_FOUND."""
    item = _item(instruments=("005930",))
    resolver = _FakeResolver({})  # empty mapping
    result = link_item(item, resolver)
    assert result.instrument_id is None
    assert result.reason == UnmappedReason.NOT_FOUND


# ---------------------------------------------------------------------------
# Failure-injection test: dependency raises during key extraction
# ---------------------------------------------------------------------------


async def test_link_item_propagates_symbol_normalizer_exception() -> None:
    """RD-5 DoD (c): `to_canonical` raising an exception propagates
    — the domain layer expects resilient adapters below it.
    This is a negative test: callers must handle this boundary."""
    item = _item(instruments=("005930",))
    resolver = _FakeResolver({})
    with patch(
        "src.foundation.research_data.domain.entity_link.to_canonical",
        side_effect=Exception("symbol_normalizer_down"),
    ):
        with pytest.raises(Exception, match="symbol_normalizer_down"):
            link_item(item, resolver)


# ---------------------------------------------------------------------------
# Failure-injection: resolver raises during lookup
# ---------------------------------------------------------------------------


async def test_link_item_propagates_resolver_exception() -> None:
    """If the resolver raises on `resolve()`, the exception propagates
    — the domain layer does not catch it. Callers must inject resilient
    adapters."""
    item = _item(instruments=("005930",))

    class _CrashingResolver:
        def resolve(self, key: EntityKey) -> str | None:
            raise RuntimeError("resolver_down")

    crashing = _CrashingResolver()
    with pytest.raises(RuntimeError, match="resolver_down"):
        link_item(item, crashing)


# ---------------------------------------------------------------------------
# Failure injection: repository `list_unlinked` raises
# ---------------------------------------------------------------------------


class _CrashingRepository:
    async def list_unlinked(
        self, conn: asyncpg.Connection, *, tenant_id: UUID, limit: int
    ) -> list[ResearchItem]:
        raise ConnectionError("db_down")

    async def save_links(
        self, conn: asyncpg.Connection, results: Sequence[EntityLinkResult]
    ) -> None:
        raise ConnectionError("db_down")


async def test_link_entities_propagates_repository_list_failure() -> None:
    """If `list_unlinked` raises, the exception propagates — no silent
    swallowing. This is a negative test confirming fail-closed behavior."""
    repo: _FakeEntityLinkRepository | _CrashingRepository = _CrashingRepository()
    resolver = _FakeResolver({})

    with pytest.raises(ConnectionError, match="db_down"):
        await link_entities(None, repo, resolver, tenant_id=_TENANT, limit=10)


# ---------------------------------------------------------------------------
# Perf assertion: `is_valid_isin` throughput budget
# ---------------------------------------------------------------------------


@pytest.mark.perf
def test_is_valid_isin_throughput_budget(
    perf_budget,
) -> None:
    """ADR-2026-09-09-C: `is_valid_isin` must handle ≥10k calls/sec.
    Budget: ≤100 µs per call (10k/sec → 100µs/call = 0.1 ms).

    task-11793: raw time.perf_counter() → PerfBudget.sample(batch=N) 전환.
    batch=500로 묶어 process_time() 15.6ms 틱 양자화 오차를 호출당 1/500로
    줄이고, cpu_ms 단위로 예산과 단위를 일치시킴.
    """
    # Valid ISINs
    valid_candidates = [
        "US0378331005",  # Apple
        "US5949181045",  # Microsoft
        "KR0000000001",  # Korean ISIN placeholder
    ]
    # Invalid candidates
    invalid_candidates = [
        "US0378331006",  # wrong checksum
        "X" * 12,  # invalid country
        "US037833100",  # too short
        "",  # empty
    ]
    all_candidates = valid_candidates + invalid_candidates
    iterations = 5000

    def run_batch() -> int:
        for _ in range(iterations):
            for cand in all_candidates:
                is_valid_isin(cand)
        return iterations

    # batch=1: run_batch() 자체가 이미 iterations×len(all_candidates) 회 호출
    sample = perf_budget.sample(run_batch, batch=1)
    total_calls = iterations * len(all_candidates)
    per_call_ms = sample.cpu_ms / total_calls

    assert per_call_ms < 0.1, (
        f"is_valid_isin took {per_call_ms * 1000:.1f} µs/call "
        f"(cpu={sample.cpu_ms:.3f}ms / {total_calls} calls — budget 0.1 ms)"
    )


# ---------------------------------------------------------------------------
# gate-red reproduction: 10x 지연 monkeypatch가 예산을 초과하면 적색
# ---------------------------------------------------------------------------


@pytest.mark.perf
def test_is_valid_isin_budget_catches_10x_delay_injection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """게이트 적색 재현(D2): CPU 예산 테스트이므로 `time.process_time`을
    monkeypatch해 1회 호출당 예산(0.1ms)을 1000배 초과하는 CPU 시간을 가장하면
    같은 단언이 AssertionError로 적색이 돼야 한다.

    단언/마커 삭제나 예산 완화가 아니라, 단언이 "제대로 작동하는지"를
    역으로 증명한다. fake process_time이 호출당 100ms를 반환하므로
    측정 예산 0.1ms를 크게 초과해 적색 확정.
    """
    _fake_time = 0.0

    def _fake_process_time() -> float:
        nonlocal _fake_time
        _fake_time += 0.1  # 100ms = 0.1s per call (1000x budget)
        return _fake_time

    monkeypatch.setattr("time.process_time", _fake_process_time)

    # 같은 측정/단언을 재사용 — budget을 초과하므로 AssertionError가 있어야 함
    valid_candidates = ["US0378331005", "US5949181045", "KR0000000001"]
    invalid_candidates = ["US0378331006", "X" * 12, "US037833100", ""]
    all_candidates = valid_candidates + invalid_candidates
    iterations = 5000

    def run_batch() -> int:
        for _ in range(iterations):
            for cand in all_candidates:
                is_valid_isin(cand)
        return iterations

    sample = PerfBudget().sample(run_batch, batch=1)
    total_calls = iterations * len(all_candidates)
    per_call_ms = sample.cpu_ms / total_calls

    assert per_call_ms < 0.1, (
        f"is_valid_isin took {per_call_ms * 1000:.1f} µs/call "
        f"(cpu={sample.cpu_ms:.3f}ms / {total_calls} calls — budget 0.1 ms)"
    )
