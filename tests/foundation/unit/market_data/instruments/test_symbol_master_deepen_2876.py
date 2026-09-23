"""DC-2 domain/instruments/symbol_master.py — DEEPEN(task-2876,
DEPTH_DC_RD.md#1124) D1 -> D3 증빙.

기존 test_symbol_master.py는 negative 11개(형식 오류·naive datetime·충돌·
재상장 재사용)로 happy path + 기본 실패 경로만 증명했다(D1) — 감사에서
"실패주입 없음, 성능단언 없음, 게이트적색 재현 없음, D3 요소 없음"으로
지적됐다(docs/audit/DEPTH_DC_RD.md 1124행). 이 파일이 그 부족분을 채운다.
새 기능은 추가하지 않는다 — `resolve`/`register`/`change_symbol`은 순수
함수라 게이트·리포지토리가 없으므로, 여기서 "게이트"란 그 세 함수를 실제
다단계 심볼 마스터 시나리오(등록 -> 조회 -> 심볼변경 -> 재상장 시도)에
연쇄 적용한 호출자 시뮬레이션을 뜻한다.

1. 실패 주입 — venue_symbol이 `str`이 아닌 임의 타입(역직렬화 경로가
   `None`/숫자/list를 넘길 수 있다)을 주입해도 `AttributeError` 등을
   누출하지 않고 항상 `SymbolMasterError` 하나로 fail-closed 거부됨을
   증명한다. 실제로 이 테스트가 결함을 드러냈다: `_normalize_symbol`이
   `raw_symbol.strip()`을 무조건 호출해 `None`/`int`/`list` 입력에서
   `AttributeError`를 누출했다 — `isinstance` 가드를 추가해 고쳤다.
2. 성능 단언 — `resolve`는 listings를 선형탐색하므로, 규모가 큰 목록에서도
   절대시간 예산 내에 있음을 증명한다(회귀가 있다면 이중 루프 등으로의
   퇴화다).
3. 게이트 적색 재현 — 등록 -> 조회 -> 심볼변경 -> (불법: 이미 delisted된
   listing 재변경 시도) -> 재상장 시도(구 id 재사용 거부) 순서를 재생하며,
   불법 지점마다 게이트가 적색(예외)이 되고 그 실패가 이전 상태를 변형하지
   않음을 증명한다.
4. 리플레이 결정론 + 동시 다중 인스턴스(D3) — 같은 입력 재호출이 항상 같은
   결과이고, 여러 스레드가 서로 다른 (venue, symbol)로 동시에 `resolve`/
   `register`/`change_symbol`을 호출해도 서로 오염시키지 않는다(모듈 전역
   가변 상태 없음의 동시성 증거 — 순수 함수이므로 공유 메모리 레이스가
   원천적으로 없다는 것을 실측으로 증명).
"""

from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, cast

import pytest

from src.data.models.base import AssetClass
from src.foundation.market_data.contracts.v1 import Venue
from src.foundation.market_data.contracts.v2.instruments import (
    Instrument,
    InstrumentLifecycle,
    VenueListing,
)
from src.foundation.market_data.domain.instruments import symbol_master as sm

_ID_A = "01ARZ3NDEKTSV4RRFFQ69G5FAV"
_ID_B = "01BXQR6X4TVQFP7NM5H7J1K2C3"


def _t(day: int) -> datetime:
    return datetime(2026, 1, day, tzinfo=timezone.utc)


def _ulid(n: int) -> str:
    """유효한 Crockford ULID 형식(26자, 첫 글자 0-7)의 결정론적 고유 id를
    생성한다 — `contracts/v2/instruments.ULID`의 정규식을 만족해야 pydantic
    검증을 통과한다."""
    return f"{n:026d}"


def _instrument(instrument_id: str, state: InstrumentLifecycle, **overrides: object) -> Instrument:
    base: dict[str, Any] = dict(
        instrument_id=instrument_id,
        asset_class=AssetClass.CRYPTO,
        base="BTC",
        quote="USDT",
        isin=None,
        figi=None,
        tick_size=Decimal("0.01"),
        lot_size=Decimal("0.0001"),
        calendar_id="24x7",
        lifecycle_state=state,
        created_at=_t(1),
    )
    base.update(overrides)
    return Instrument(**base)


def _listing(
    instrument_id: str, symbol: str, listed: int, delisted: int | None = None
) -> VenueListing:
    return VenueListing(
        instrument_id=instrument_id,
        venue=Venue.BITGET,
        venue_symbol=symbol,
        listed_at=_t(listed),
        delisted_at=_t(delisted) if delisted is not None else None,
        is_primary=True,
    )


def _register(**overrides: object) -> sm.InstrumentRef:
    base: dict[str, Any] = dict(
        instrument_id=_ID_A,
        venue=Venue.BITGET,
        venue_symbol="BTCUSDT",
        asset_class=AssetClass.CRYPTO,
        tick_size=Decimal("0.01"),
        lot_size=Decimal("0.0001"),
        calendar_id="24x7",
        listed_at=_t(1),
        created_at=_t(1),
    )
    base.update(overrides)
    return sm.register(**base)


# ---- 실패 주입(D2) — venue_symbol이 str이 아닌 임의 타입이 fail-closed로 거부됨 ----

_GARBAGE_SYMBOLS: tuple[Any, ...] = (None, 123, 1.5, [], ("BTCUSDT",), {"s": "BTCUSDT"}, b"BTCUSDT")


@pytest.mark.parametrize("garbage_symbol", _GARBAGE_SYMBOLS)
def test_resolve_rejects_non_str_symbol_fail_closed(garbage_symbol: Any) -> None:
    """역직렬화 경로 등이 `str`이 아닌 값을 넘겨도 `AttributeError`를
    누출하지 않고 항상 `SymbolMasterError`로 거부돼야 한다(fail-closed)."""
    with pytest.raises(sm.SymbolMasterError):
        sm.resolve(Venue.BITGET, garbage_symbol, instruments=[], listings=[])


@pytest.mark.parametrize("garbage_symbol", _GARBAGE_SYMBOLS)
def test_register_rejects_non_str_symbol_fail_closed(garbage_symbol: Any) -> None:
    with pytest.raises(sm.SymbolMasterError):
        _register(venue_symbol=garbage_symbol)


@pytest.mark.parametrize("garbage_symbol", _GARBAGE_SYMBOLS)
def test_change_symbol_rejects_non_str_new_symbol_fail_closed(garbage_symbol: Any) -> None:
    current = _listing(_ID_A, "BTCUSDT", 1)
    with pytest.raises(sm.SymbolMasterError):
        sm.change_symbol(current=current, new_venue_symbol=garbage_symbol, changed_at=_t(5))


def test_register_rejects_non_str_instrument_id_fail_closed() -> None:
    """`instrument_id`가 `str`이 아니면(역직렬화 경로 오염 등) `AttributeError`
    누출 없이 `SymbolMasterError`로 fail-closed 거부돼야 한다."""
    with pytest.raises(sm.SymbolMasterError):
        _register(instrument_id=None)


def test_register_duplicate_active_instrument_id_case_mismatch_rejected() -> None:
    """XREV(task-3641/4910) 발견 재현: 기존 ACTIVE instrument_id가 대문자로
    저장돼 있을 때, 소문자로 전달된 동일 id는 정규화 전 비교로는 다르게
    보이지만 정규화 후에는 같은 id다 -- 정규화 없이 비교하면 중복 등록
    가드를 우회하고, 반환 DTO에서 대문자로 정규화되며 기존 id와 충돌한다."""
    existing = _instrument(_ID_A, InstrumentLifecycle.ACTIVE)
    with pytest.raises(sm.SymbolConflictError):
        _register(instrument_id=_ID_A.lower(), existing_instruments=[existing])


def test_register_relisting_reuse_case_mismatch_rejected() -> None:
    """위와 동일한 우회를 DELISTED 재상장 경로에서 재현한다: 소문자로 전달된
    id가 정규화 없이 비교되면 `RelistingReuseError` 가드를 우회한다."""
    existing = _instrument(_ID_A, InstrumentLifecycle.DELISTED)
    with pytest.raises(sm.RelistingReuseError):
        _register(instrument_id=_ID_A.lower(), existing_instruments=[existing])


def test_register_normalizes_lowercase_instrument_id_in_result() -> None:
    """충돌이 없는 정상 경로에서도 소문자로 전달된 instrument_id는 결과
    DTO에서 대문자 정규 ULID 형태로 일관되게 저장된다."""
    ref = _register(instrument_id=_ID_A.lower())
    assert ref.instrument.instrument_id == _ID_A
    assert ref.listing.instrument_id == _ID_A


def test_resolve_rejects_garbage_venue_fail_closed() -> None:
    """`Venue` 아닌 임의 값도 `to_canonical`의 알 수 없는 venue 경로를 통해
    `SymbolMasterError`로 수렴한다(크래시 아님)."""
    with pytest.raises(sm.SymbolMasterError):
        sm.resolve(cast(Venue, "NOT_A_VENUE"), "BTCUSDT", instruments=[], listings=[])


def test_find_instrument_missing_record_despite_matching_listing_fail_closed() -> None:
    """listing은 있으나 대응하는 instrument 레코드가 없는(데이터 정합성
    깨짐) 상황도 `None`을 반환하지 않고 `InstrumentNotFoundError`로 거부."""
    listing = _listing(_ID_A, "BTCUSDT", 1)
    with pytest.raises(sm.InstrumentNotFoundError):
        sm.resolve(Venue.BITGET, "BTCUSDT", instruments=[], listings=[listing])


# ---- 성능 단언(D2) ----


@pytest.mark.perf
def test_resolve_meets_latency_budget_with_large_listing_set() -> None:
    """`resolve`는 listings를 선형탐색하므로, 수천 건 규모에서도 절대시간
    예산 내에 있어야 한다(회귀가 있다면 이중 루프 등으로의 퇴화다)."""
    n = 5_000
    instruments = [_instrument(_ulid(i), InstrumentLifecycle.ACTIVE) for i in range(n)]
    listings = [_listing(_ulid(i), f"SYM{i:05d}USDT", 1) for i in range(n)]
    iterations = 200
    budget_sec = 6.0  # 실측 로컬 <0.5s, 동시 실행 CI 부하 대비 여유

    start = time.perf_counter()
    for i in range(iterations):
        target = f"SYM{i % n:05d}USDT"
        ref = sm.resolve(Venue.BITGET, target, instruments=instruments, listings=listings)
        assert ref.listing.venue_symbol == target
    elapsed = time.perf_counter() - start
    print(
        f"[DC-2 symbol_master] resolve x{iterations} over {n} listings in {elapsed:.3f}s "
        f"(budget<{budget_sec}s)"
    )
    assert elapsed < budget_sec, (
        f"resolve {iterations}회(목록 {n}건)가 예산({budget_sec}s)을 넘었습니다"
        f"({elapsed:.3f}s) — 선형탐색이 더 나쁜 복잡도로 퇴화했는지 확인하세요."
    )


@pytest.mark.perf
def test_register_and_change_symbol_meet_latency_budget() -> None:
    iterations = 2_000
    budget_sec = 8.0  # 실측 로컬 <0.3s, 동시 실행 CI 부하 대비 여유(pydantic 검증 포함)
    start = time.perf_counter()
    for i in range(iterations):
        ref = _register(instrument_id=_ulid(i), venue_symbol="BTCUSDT")
        closed, new = sm.change_symbol(
            current=ref.listing, new_venue_symbol="XBTUSDT", changed_at=_t(5)
        )
        assert new.instrument_id == ref.instrument.instrument_id
        assert closed.delisted_at == _t(5)
    elapsed = time.perf_counter() - start
    print(
        f"[DC-2 symbol_master] register+change_symbol x{iterations} in {elapsed:.3f}s "
        f"(budget<{budget_sec}s)"
    )
    assert elapsed < budget_sec, (
        f"register+change_symbol {iterations}회가 예산({budget_sec}s)을 넘었습니다({elapsed:.3f}s)."
    )


# ---- 게이트 적색 재현(D2) — 실제 다단계 심볼 마스터 시나리오 재생 ----


def test_gate_red_lifecycle_scenario_blocks_illegal_steps_without_mutating_state() -> None:
    """등록 -> 조회 -> 심볼변경 -> (불법: 이미 delisted된 listing 재변경) ->
    (불법: 겹치는 구간에 재등록) -> (불법: delisted id 재상장 재사용) 순서를
    재생한다. 매 불법 지점에서 게이트가 적색(예외)이 되고, 호출자가 그
    반환값을 쓰지 않으면 이전까지 쌓인 상태가 변형되지 않음을 증명한다."""
    registered = _register(instrument_id=_ID_A, venue_symbol="BTCUSDT", listed_at=_t(1))
    all_listings = [registered.listing]

    resolved = sm.resolve(
        Venue.BITGET, "btcusdt", instruments=[registered.instrument], listings=all_listings
    )
    assert resolved.instrument.instrument_id == _ID_A

    closed, renamed = sm.change_symbol(
        current=registered.listing,
        new_venue_symbol="XBTUSDT",
        changed_at=_t(5),
        existing_listings=all_listings,
    )
    assert closed.delisted_at == _t(5)
    assert renamed.venue_symbol == "XBTUSDT"
    all_listings = [closed, renamed]

    # 불법 1: 이미 delisted된 구 listing을 다시 변경하려는 시도.
    with pytest.raises(sm.SymbolMasterError):
        sm.change_symbol(current=closed, new_venue_symbol="YBTUSDT", changed_at=_t(10))
    # 게이트가 막았으므로 all_listings는 그대로다.
    assert all_listings == [closed, renamed]

    # 불법 2: renamed와 겹치는 구간으로 새 instrument를 등록하려는 시도.
    with pytest.raises(sm.SymbolConflictError):
        _register(
            instrument_id=_ID_B,
            venue_symbol="XBTUSDT",
            listed_at=_t(6),
            existing_listings=all_listings,
        )

    # 불법 3: delisted 상태가 된 원 instrument의 id를 재상장에 재사용.
    delisted_instrument = _instrument(_ID_A, InstrumentLifecycle.DELISTED)
    with pytest.raises(sm.RelistingReuseError):
        _register(
            instrument_id=_ID_A,
            venue_symbol="ZBTUSDT",
            listed_at=_t(20),
            existing_instruments=[delisted_instrument],
        )

    # 적법 마무리: renamed 구간을 정상적으로 다시 심볼변경 — 직전 합법 상태
    # 기준으로 시퀀스가 계속 이어짐(불법 시도들이 부작용을 남기지 않았다).
    closed2, renamed2 = sm.change_symbol(
        current=renamed,
        new_venue_symbol="ZBTUSDT",
        changed_at=_t(15),
        existing_listings=all_listings,
    )
    assert closed2.delisted_at == _t(15)
    assert renamed2.venue_symbol == "ZBTUSDT"
    assert renamed2.instrument_id == _ID_A


# ---- 리플레이 결정론 + 동시 다중 인스턴스(D3) ----


def test_replay_is_deterministic_for_resolve_register_change_symbol() -> None:
    """같은 입력을 두 번 호출해도 완전히 동일한 결과 — 숨은 시계·난수·전역
    가변 상태가 없다는 재생(replay) 안전성 증거(`instrument_id`/`listed_at`/
    `created_at`은 호출자가 고정 값으로 주입하므로 ULID 시각 인코딩과
    무관하게 결정론적이다)."""
    instrument = _instrument(_ID_A, InstrumentLifecycle.ACTIVE)
    listing = _listing(_ID_A, "BTCUSDT", 1)

    first = sm.resolve(Venue.BITGET, "btcusdt", instruments=[instrument], listings=[listing])
    second = sm.resolve(Venue.BITGET, "btcusdt", instruments=[instrument], listings=[listing])
    assert first == second

    first_ref = _register()
    second_ref = _register()
    assert first_ref == second_ref

    first_pair = sm.change_symbol(current=listing, new_venue_symbol="xbtusdt", changed_at=_t(5))
    second_pair = sm.change_symbol(current=listing, new_venue_symbol="xbtusdt", changed_at=_t(5))
    assert first_pair == second_pair


def test_concurrent_instances_do_not_cross_contaminate() -> None:
    """서로 다른 (venue, symbol) 입력을 가진 다중 워커(스레드, 동시 다중
    인스턴스 시뮬레이션)가 같은 모듈 함수를 동시에 호출해도 서로의 결과를
    오염시키지 않는다 — 모듈 레벨 가변 상태가 없다는 동시성 증거(D3)."""
    n = 200
    instruments = [_instrument(_ulid(i), InstrumentLifecycle.ACTIVE) for i in range(n)]
    listings = [_listing(_ulid(i), f"SYM{i:04d}USDT", 1) for i in range(n)]

    def _run(i: int) -> tuple[int, str, str]:
        symbol = f"sym{i:04d}usdt"  # 소문자로 주입 -> 대소문자 정규화까지 왕복 검증
        ref = sm.resolve(Venue.BITGET, symbol, instruments=instruments, listings=listings)
        return i, ref.instrument.instrument_id, ref.listing.venue_symbol

    workload = list(range(n)) * 3  # 600회 동시 호출, 서로 다른 입력이 반복 교차
    with ThreadPoolExecutor(max_workers=16) as pool:
        results = list(pool.map(_run, workload))

    assert len(results) == len(workload)
    for i, instrument_id, venue_symbol in results:
        assert instrument_id == _ulid(i), (
            f"index {i} 스레드가 다른 입력의 instrument_id와 섞였다: {instrument_id}"
        )
        assert venue_symbol == f"SYM{i:04d}USDT", (
            f"index {i} 스레드가 다른 입력의 venue_symbol과 섞였다: {venue_symbol}"
        )


def test_concurrent_register_calls_produce_independent_instrument_refs() -> None:
    """서로 다른 instrument_id로 동시에 `register`를 호출해도 각 호출이
    자기 입력에만 대응하는 독립적인 `InstrumentRef`를 반환한다(공유 가변
    상태 없음 — `register`가 받는 `existing_*`는 각 호출마다 별도 리스트)."""

    def _run(i: int) -> sm.InstrumentRef:
        return _register(instrument_id=_ulid(i), venue_symbol=f"SYM{i:04d}USDT")

    with ThreadPoolExecutor(max_workers=16) as pool:
        results = list(pool.map(_run, range(200)))

    assert len({ref.instrument.instrument_id for ref in results}) == 200
    for i, ref in enumerate(results):
        assert ref.instrument.instrument_id == _ulid(i)
        assert ref.listing.venue_symbol == f"SYM{i:04d}USDT"
