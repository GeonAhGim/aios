"""LA-9 ports/*.py 구조적 계약 테스트.

Spec: docs/specs/L4_market_data_positions_ledger_v1.0.md#§2.2, §9.2 LA-9.

`@runtime_checkable` Protocol의 `isinstance()`는 메서드 **이름**만 확인한다 —
파라미터·반환 타입은 mypy(정적)가 확인한다
(`tests/foundation/unit/positions/test_ports_protocol.py`와 같은 패턴). 그래서
negative test는 세 종류다: (1) 메서드 하나가 빠진 구현은 isinstance()에서부터
False가 되는 fail-closed 사례(`CandleStore`, `ReferenceRepository` 두 포트에서
각각 증명 — 단일 포트의 우연이 아님), (2) 메서드는 다 갖췄지만 DTO 대신 dict를
돌려주는 구현은 isinstance()를 통과해도 그 결과가 계약 DTO(`IngestBatchResult`)
검증은 통과하지 못한다는 사례, (3) `async def` 대신 `def`로 구현해도
isinstance()는 이름만 보므로 통과시킨다는(게이트가 초록으로 남는다는) 실제
결함 클래스.

DEPTH 감사(task-2723, docs/audit/DEPTH_LA_LB_LC.md 417)가 지적한 4개 공백을
채운다: negative 3건 이상, monkeypatch failure-injection, 수치 성능 단언,
게이트 적색(구조 검사 통과 후 런타임에야 드러나는 결함) 재현.
"""

from __future__ import annotations

import time
from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from src.foundation.market_data.contracts.v1 import IngestBatchResult
from src.foundation.market_data.ports.batch_repository import BatchRepository
from src.foundation.market_data.ports.calendar_repository import CalendarRepository
from src.foundation.market_data.ports.candle_store import CandleStore
from src.foundation.market_data.ports.ingest_source import IngestSource
from src.foundation.market_data.ports.reference_repository import ReferenceRepository


def _now() -> datetime:
    return datetime(2026, 9, 3, 0, 0, tzinfo=timezone.utc)


class _FullCandleStore:
    async def upsert_batch(self, conn, batch_id, candles): ...
    async def quarantine(self, conn, batch_id, candles, issues): ...
    async def query(self, conn, key, start, end, as_of): ...
    async def last_open_time(self, conn, key): ...
    async def read_candles_columnar(self, conn, key, start, end, as_of): ...


class _MissingLastOpenTimeCandleStore:
    """`last_open_time`이 빠진 불완전 구현 — 포트를 만족하지 못해야 한다."""

    async def upsert_batch(self, conn, batch_id, candles): ...
    async def quarantine(self, conn, batch_id, candles, issues): ...
    async def query(self, conn, key, start, end, as_of): ...


class _FullReferenceRepository:
    async def get_instrument(self, conn, venue, canonical, at): ...
    async def register(self, conn, cmd): ...
    async def add_alias(self, conn, instrument_id, venue, venue_symbol): ...
    async def list_actions(self, conn, instrument_id): ...
    async def record_action(self, conn, action): ...


class _MissingRecordActionReferenceRepository:
    """`record_action`이 빠진 불완전 구현 — `CandleStore`가 아닌 다른
    포트에서도 fail-closed가 우연이 아님을 보이는 세 번째 negative test용."""

    async def get_instrument(self, conn, venue, canonical, at): ...
    async def register(self, conn, cmd): ...
    async def add_alias(self, conn, instrument_id, venue, venue_symbol): ...
    async def list_actions(self, conn, instrument_id): ...


class _SyncLastOpenTimeCandleStore:
    """나머지 메서드는 `async def`지만 `last_open_time`만 실수로 동기
    `def`로 구현한 어댑터 — `isinstance()`는 멤버가 코루틴 함수인지는
    보지 않고 이름 존재만 확인하므로 이 구조적 결함도 통과시킨다(게이트
    적색 재현용 fixture)."""

    async def upsert_batch(self, conn, batch_id, candles): ...
    async def quarantine(self, conn, batch_id, candles, issues): ...
    async def query(self, conn, key, start, end, as_of): ...
    async def read_candles_columnar(self, conn, key, start, end, as_of): ...

    def last_open_time(self, conn, key):
        return None


class _InjectableCandleStore:
    """`test_runtime_method_removal_flips_isinstance_to_false` 전용 — 다른
    테스트가 이 클래스를 isinstance()로 먼저 검사하면 `abc.ABCMeta`의
    양성 판정 캐시(`_abc_cache`)가 굳어 이후 monkeypatch로 지운 메서드가
    반영되지 않는다(캐시는 `register()`/새 클래스 생성 시점에만 무효화된다).
    그래서 이 fixture는 다른 테스트와 공유하지 않는 전용 클래스로 둔다."""

    async def upsert_batch(self, conn, batch_id, candles): ...
    async def quarantine(self, conn, batch_id, candles, issues): ...
    async def query(self, conn, key, start, end, as_of): ...
    async def last_open_time(self, conn, key): ...
    async def read_candles_columnar(self, conn, key, start, end, as_of): ...


class _FullCalendarRepository:
    async def load(self, conn, venue, year): ...
    async def upsert_days(self, conn, venue, days): ...


class _FullIngestSource:
    async def fetch_candles(self, venue, raw_symbol, tf, start, end): ...


class _FullBatchRepository:
    async def create(self, conn, batch): ...
    async def add_issues(self, conn, batch_id, issues): ...
    async def get(self, conn, batch_id, tenant_id): ...
    async def create_tick_batch(self, conn, batch): ...
    async def get_tick_batch(self, conn, batch_id, tenant_id): ...


class _DictReturningBatchRepository:
    """메서드 이름은 전부 갖췄으니 `isinstance()`는 통과하지만, `get`이
    `IngestBatchResult` 대신 얕은 dict를 돌려준다 — mypy가 없으면 구조 검사만
    으로는 이 차이를 잡지 못한다는 것을 보이는 fixture."""

    async def create(self, conn, batch): ...
    async def add_issues(self, conn, batch_id, issues): ...
    async def create_tick_batch(self, conn, batch): ...
    async def get_tick_batch(self, conn, batch_id, tenant_id): ...

    async def get(self, conn, batch_id, tenant_id):
        return {"batch_id": str(batch_id)}


def test_full_implementations_satisfy_their_ports() -> None:
    assert isinstance(_FullCandleStore(), CandleStore)
    assert isinstance(_FullReferenceRepository(), ReferenceRepository)
    assert isinstance(_FullCalendarRepository(), CalendarRepository)
    assert isinstance(_FullIngestSource(), IngestSource)
    assert isinstance(_FullBatchRepository(), BatchRepository)


def test_incomplete_implementation_fails_port_check() -> None:
    """포트 메서드 하나 누락 → isinstance() False(fail-closed 구조 증명)."""
    assert not isinstance(_MissingLastOpenTimeCandleStore(), CandleStore)


async def test_dict_returning_fake_satisfies_isinstance_but_not_the_dto() -> None:
    """DoD negative test: dict를 돌려주는 가짜 구현은 구조적으로는 포트를
    만족한다고 판정되지만(메서드 이름만 검사하므로), 그 결과값은 계약 DTO
    검증을 통과하지 못한다 — Protocol을 "진짜로" 만족한다고 볼 수 없다."""
    fake = _DictReturningBatchRepository()
    assert isinstance(fake, BatchRepository)

    result = await fake.get(conn=None, batch_id=_now(), tenant_id=None)
    assert isinstance(result, dict)
    with pytest.raises(ValidationError):
        IngestBatchResult.model_validate(result)


def test_reference_repository_missing_record_action_fails_port_check() -> None:
    """세 번째 negative test(D2 negative>=3): `CandleStore`뿐 아니라
    `ReferenceRepository`에서도 메서드 하나 누락이 isinstance()를 False로
    뒤집는다 — fail-closed가 특정 포트 하나의 우연이 아님을 증명한다."""
    assert not isinstance(_MissingRecordActionReferenceRepository(), ReferenceRepository)


def test_runtime_method_removal_flips_isinstance_to_false(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """failure-injection: 운영 중 잘못된 patch/mock이 실제 구현 클래스에서
    메서드 하나를 지워버리는 사고를 monkeypatch로 재현한다 — 지운 뒤에는
    (이 클래스에 대해 처음 수행되는) isinstance() 판정이 곧바로 False가
    되어야 한다(fail-closed). `_InjectableCandleStore` 전용 클래스를 쓰는
    이유는 클래스 docstring 참고 — `abc.ABCMeta`가 이전 양성 판정을 캐시해
    같은 클래스를 재사용하면 이 삭제가 반영되지 않는다."""
    monkeypatch.delattr(_InjectableCandleStore, "last_open_time")

    assert not isinstance(_InjectableCandleStore(), CandleStore)


def test_isinstance_checks_over_thousands_of_instances_stay_fast() -> None:
    """수치 성능 단언: `runtime_checkable` Protocol의 isinstance()는 멤버
    이름 개수에 비례하는 저비용 해시조회여야 한다 — 이 전제가 깨지면(예:
    누군가 실수로 무거운 `__instancecheck__`/검증 로직을 끼워 넣으면) 포트
    판정이 호출되는 모든 경로(등록·조회·인제스트)가 함께 느려진다. 5개
    포트 x 10,000회 = 50,000회 isinstance() 호출이 1초 미만에 끝나야 한다."""
    fakes: list[tuple[object, type]] = [
        (_FullCandleStore(), CandleStore),
        (_FullReferenceRepository(), ReferenceRepository),
        (_FullCalendarRepository(), CalendarRepository),
        (_FullIngestSource(), IngestSource),
        (_FullBatchRepository(), BatchRepository),
    ]

    start = time.perf_counter()
    for _ in range(10_000):
        for instance, port in fakes:
            assert isinstance(instance, port)
    elapsed = time.perf_counter() - start

    assert elapsed < 1.0


class _MissingLoadCalendarRepository:
    """`load` 메서드가 빠진 CalendarRepository 불완전 구현 — 두 번째 포트
    타입에서 fail-closed가 우연이 아님을 보이는 추가 negative test."""

    async def upsert_days(self, conn, venue, days): ...


class _MissingFetchIngestSource:
    """`fetch_candles` 메서드가 빠진 IngestSource 불완전 구현 — 세 번째
    포트 타입에서 fail-closed가 우연이 아님을 보이는 추가 negative test."""


def test_calendar_repository_missing_load_fails_port_check() -> None:
    """추가 negative test 1건: `CalendarRepository`에서 `load` 메서드
    누락 → isinstance() False. CandleStore/ReferenceRepository에 국한되지
    않는 fail-closed 행동을 증명한다."""
    assert not isinstance(_MissingLoadCalendarRepository(), CalendarRepository)


def test_ingest_source_missing_fetch_fails_port_check() -> None:
    """추가 negative test 2건: `IngestSource`에서 `fetch_candles` 메서드
    누락 → isinstance() False. 구조적 계약이 모든 포트 타입에서 fail-closed
    됨을 확인한다."""
    assert not isinstance(_MissingFetchIngestSource(), IngestSource)


async def test_upsert_batch_failure_injection_raises() -> None:
    """실패주입 케이스 1건: `CandleStore.upsert_batch`가 의존하는
    데이터베이스 레이어에서 예외가 발생하는 상황을 monkeypatch로 재현한다.
    어댑터가 이 예외를 silently吃掉하면 배치 손실을 알 수 없으므로,
    isinstance() 통과한 구현체가 실제 예외를 그대로 전파하는지 검증한다."""
    from src.foundation.market_data.ports.candle_store import CandleStore

    class _FailingCandleStore:
        async def upsert_batch(self, conn, batch_id, candles):
            raise ConnectionRefusedError("simulated db connection lost")

        async def quarantine(self, conn, batch_id, candles, issues): ...

        async def query(self, conn, key, start, end, as_of):
            return []

        async def last_open_time(self, conn, key):
            return None

        async def read_candles_columnar(self, conn, key, start, end, as_of):
            from src.foundation.market_data.domain.candle_columns import (
                CandleColumns,
            )

            return CandleColumns([], [], [], [], [], [], [])

    fake = _FailingCandleStore()
    assert isinstance(fake, CandleStore)  # 구조는 만족

    with pytest.raises(ConnectionRefusedError):
        await fake.upsert_batch(conn=None, batch_id=None, candles=[])


async def test_sync_method_silently_satisfies_async_protocol_gate_red() -> None:
    """게이트 적색 재현: `isinstance()`는 멤버가 코루틴 함수(`async def`)인지
    검사하지 않고 이름 존재만 본다. 그래서 실수로 `last_open_time`을 동기
    `def`로 구현한 어댑터도 isinstance() 관문은 초록으로 통과시킨다 — 결함이
    바로 드러나지 않고, 실제로 호출부가 `await`하는 순간에야("게이트가
    초록이던 코드가 실행 중 적색이 되는" 실제 결함 클래스) `TypeError`로
    터진다. 구조 검사만으로는 이 간극을 잡지 못하고 mypy --strict(반환 타입
    `Coroutine[..., X]` 정적 검사)가 막아야 한다는 파일 전체 취지를 실제
    재현으로 뒷받침한다."""
    fake = _SyncLastOpenTimeCandleStore()
    assert isinstance(fake, CandleStore)  # 구조 검사(게이트)는 초록

    with pytest.raises(TypeError):
        await fake.last_open_time(conn=None, key=None)  # 실행 시점에 적색
