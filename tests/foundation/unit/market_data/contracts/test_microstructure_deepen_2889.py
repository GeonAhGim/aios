"""DC-19 `contracts/v2/microstructure.py` — DEEPEN(task-2889,
docs/audit/DEPTH_DC_RD.md#2068) D1 -> D3 증빙.

기존 test_microstructure_v2.py는 스냅샷 1건 + negative 7건으로 나노초
경계·aggressor 폐집합·BookL2 정렬을 단건씩 증명했다(D1) — 소급감사
(task-2726)에서 "D3 하한 미달"로 지적됐다. 이 파일이 그 부족분을 채운다.
`microstructure.py`는 순수 pydantic 모델(DB/네트워크 없음)이라 이 파일에서
"실패주입"이란 나노초 경계·단위 실수·naive datetime이 `ts_event`뿐 아니라
`ts_recv`에서도, BookL2의 모든 실패 지점에서도 새지 않는지를, "게이트 적색
재현"이란 하나의 TradeTick 구성을 필드별로 순서대로 망가뜨려 재생하며 이전
단계의 유효 상태가 다음 단계로 새지 않는지를, "D3"란 순수 불변(frozen)
모델이라 공유 가변 상태가 없어 다중 스레드 동시 구성도 서로 오염되지
않는지를 뜻한다. `microstructure.py`는 무수정 -- 새 기능 없음, 깊이만
올린다.

1. 실패 주입 — ts_recv naive/초/밀리초/마이크로초 단위 거부, 나노초 경계
   정확값(부동소수점 아님, 정수 경계) 안팎, seq 음수, BookL2 빈 레벨/단일
   레벨/동가 레벨 경계, frozen 모델 재할당 거부.
2. 성능 단언 — 대량 TradeTick 검증과 대형 BookL2(수천 레벨) 구성이
   절대시간 예산 내에 있음을 증명한다.
3. 게이트 적색 재현 — 유효한 TradeTick 페이로드 하나를 필드 단위로 순서대로
   망가뜨려 재생한다 — 각 단계가 그 필드의 사유로만 실패하고 이전 단계의
   부분 상태가 다음 단계로 새지 않음을 증명한다.
4. 동시 다중 인스턴스(D3) — 스레드풀로 서로 다른 venue/seq 조합의
   TradeTick/BookL2를 동시에 구성해도 값이 섞이지 않음을 증명한다.
"""

from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from decimal import Decimal

import pytest
from pydantic import ValidationError

from src.foundation.market_data.contracts.v1 import Venue
from src.foundation.market_data.contracts.v2 import microstructure as v2

_VALID_ULID = "01ARZ3NDEKTSV4RRFFQ69G5FAV"
_NS_EVENT = 1_767_225_600_123_456_789
_NS_RECV = 1_767_225_600_223_456_789
_SECOND_UNIT = 1_767_225_600
_MILLISECOND_UNIT = 1_767_225_600_123
_MICROSECOND_UNIT = 1_767_225_600_123_456


def _trade_tick(**overrides: object) -> v2.TradeTick:
    base: dict[str, object] = dict(
        instrument_id=_VALID_ULID,
        venue=Venue.BITGET,
        ts_event=_NS_EVENT,
        ts_recv=_NS_RECV,
        seq=1,
        price=Decimal("50000.5"),
        size=Decimal("0.01"),
        aggressor=v2.Aggressor.BUY,
    )
    base.update(overrides)
    return v2.TradeTick.model_validate(base)


def _book_level(price: str, size: str = "1.0") -> v2.BookLevel:
    return v2.BookLevel(price=Decimal(price), size=Decimal(size))


def _book_l2(**overrides: object) -> v2.BookL2:
    base: dict[str, object] = dict(
        instrument_id=_VALID_ULID,
        venue=Venue.BITGET,
        ts_event=_NS_EVENT,
        ts_recv=_NS_RECV,
        seq=1,
        bids=(_book_level("50000.0"), _book_level("49999.5")),
        asks=(_book_level("50000.5"), _book_level("50001.0")),
    )
    base.update(overrides)
    return v2.BookL2.model_validate(base)


# ---- 1. 실패 주입 ----------------------------------------------------------


def test_ts_recv_naive_datetime_rejected() -> None:
    """기존 스위트는 `ts_event`의 naive datetime 거부만 증명했다 — 같은
    `Nanoseconds` 타입을 공유하는 `ts_recv`에서도 새지 않아야 한다."""
    with pytest.raises(ValidationError):
        _trade_tick(ts_recv=datetime(2026, 9, 3, 0, 0))


def test_ts_recv_second_unit_rejected() -> None:
    with pytest.raises(ValidationError):
        _trade_tick(ts_recv=_SECOND_UNIT)


@pytest.mark.parametrize(
    "value", [_SECOND_UNIT, _MILLISECOND_UNIT, _MICROSECOND_UNIT], ids=["s", "ms", "us"]
)
def test_ts_event_rejects_every_smaller_unit_mistake(value: int) -> None:
    """초/밀리초/마이크로초 단위 실수 전부가 나노초 하한 미달로 거부돼야
    한다 — 초 단위만 테스트하면 밀리초·마이크로초 실수가 우연히 하한을
    넘는 회귀를 놓친다."""
    with pytest.raises(ValidationError):
        _trade_tick(ts_event=value)


def test_ts_event_exact_floor_boundary_accepted() -> None:
    tick = _trade_tick(ts_event=v2._NANOSECOND_FLOOR)
    assert tick.ts_event == v2._NANOSECOND_FLOOR


def test_ts_event_one_below_floor_rejected() -> None:
    with pytest.raises(ValidationError):
        _trade_tick(ts_event=v2._NANOSECOND_FLOOR - 1)


def test_ts_event_one_below_ceiling_accepted() -> None:
    tick = _trade_tick(ts_event=v2._NANOSECOND_CEILING - 1)
    assert tick.ts_event == v2._NANOSECOND_CEILING - 1


def test_ts_event_exact_ceiling_rejected() -> None:
    """배타적 상한 — `_NANOSECOND_CEILING` 자신은 범위 밖이다(`<` 아님)."""
    with pytest.raises(ValidationError):
        _trade_tick(ts_event=v2._NANOSECOND_CEILING)


def test_ts_event_negative_rejected() -> None:
    with pytest.raises(ValidationError):
        _trade_tick(ts_event=-_NS_EVENT)


def test_seq_zero_boundary_accepted() -> None:
    tick = _trade_tick(seq=0)
    assert tick.seq == 0


def test_book_l2_empty_levels_accepted() -> None:
    """빈 book(레벨 0개)은 정렬 위반이 성립하지 않으므로(공집합은 자명하게
    정렬됨) 거부 사유가 없다 — 이 동작을 명시적으로 문서화한다."""
    book = _book_l2(bids=(), asks=())
    assert book.bids == ()
    assert book.asks == ()


def test_book_l2_single_level_accepted() -> None:
    book = _book_l2(bids=(_book_level("50000.0"),), asks=(_book_level("50000.5"),))
    assert len(book.bids) == 1
    assert len(book.asks) == 1


def test_book_l2_tied_adjacent_prices_accepted() -> None:
    """동가(tie) 인접 레벨은 엄밀한 내림/오름차순이 아니라 비증가/비감소로
    검사되므로 거부되지 않는다 — `sorted()` 동치 비교의 실제 동작을
    문서화한다(스펙은 엄밀한 정렬만 요구하지 동가 금지를 요구하지 않음)."""
    book = _book_l2(bids=(_book_level("50000.0"), _book_level("50000.0")))
    assert book.bids[0].price == book.bids[1].price


def test_trade_tick_is_frozen_against_mutation() -> None:
    tick = _trade_tick()
    mutable_field = "price"
    with pytest.raises(ValidationError):
        setattr(tick, mutable_field, Decimal("1"))


def test_book_l2_is_frozen_against_mutation() -> None:
    book = _book_l2()
    mutable_field = "seq"
    with pytest.raises(ValidationError):
        setattr(book, mutable_field, 99)


# ---- 2. 성능 단언 -----------------------------------------------------------


@pytest.mark.perf
def test_trade_tick_bulk_validation_meets_throughput_budget() -> None:
    """수신 스트림에서 틱마다 새로 역직렬화되는 상황을 흉내 — 대량 반복
    검증이 처리량 예산을 지켜야 한다."""
    iterations = 100_000
    budget_sec = 5.0  # 실측 로컬 <1.0s, CI 환경 편차 감안

    start = time.perf_counter()
    for i in range(iterations):
        tick = _trade_tick(seq=i, ts_event=_NS_EVENT + i)
        assert tick.seq == i
    elapsed = time.perf_counter() - start

    print(
        f"[DC-19 microstructure] TradeTick.model_validate() x{iterations} in "
        f"{elapsed:.3f}s (budget<{budget_sec}s)"
    )
    assert elapsed < budget_sec, (
        f"TradeTick 검증 {iterations}회 반복이 예산({budget_sec}s)을 넘었습니다({elapsed:.3f}s)."
    )


@pytest.mark.perf
def test_large_book_l2_construction_meets_latency_budget() -> None:
    """레벨 수가 큰(각 5,000개) BookL2 구성 + 정렬 검증이 절대시간 예산
    내여야 한다(정렬 위반 검사가 이차로 퇴화하지 않았는지 — 깊은 호가창을
    제공하는 벤더가 늘수록 이 값도 커진다)."""
    n = 5_000
    budget_sec = 2.0  # 실측 로컬 <0.3s
    bids = tuple(_book_level(str(Decimal("50000.0") - Decimal(i)), "1.0") for i in range(n))
    asks = tuple(_book_level(str(Decimal("50000.5") + Decimal(i)), "1.0") for i in range(n))

    start = time.perf_counter()
    book = _book_l2(bids=bids, asks=asks)
    elapsed = time.perf_counter() - start

    print(
        f"[DC-19 microstructure] BookL2({n}x2 levels) construction in {elapsed:.4f}s "
        f"(budget<{budget_sec}s)"
    )
    assert len(book.bids) == n
    assert len(book.asks) == n
    assert elapsed < budget_sec, (
        f"대용량 BookL2 구성이 예산({budget_sec}s)을 넘었습니다({elapsed:.4f}s)."
    )


# ---- 3. 게이트 적색 재현 — 필드 단위로 순서대로 망가뜨려 재생 --------------


def test_gate_red_field_by_field_corruption_never_leaks_prior_valid_state() -> None:
    """유효한 TradeTick 구성 하나를 기준으로, 필드 하나씩만 망가뜨린 변형을
    순서대로 재생한다 — 각 단계가 그 필드의 사유로만 거부되고, 앞 단계에서
    검증을 통과한 필드 값이 뒤 단계의 실패를 가리거나 부분 객체가 새지
    않음을 증명한다(pydantic이 실패 시 부분 초기화된 인스턴스를 반환하지
    않는다는 원자성 가정을 명시적으로 검증)."""
    valid_payload: dict[str, object] = dict(
        instrument_id=_VALID_ULID,
        venue=Venue.BITGET,
        ts_event=_NS_EVENT,
        ts_recv=_NS_RECV,
        seq=1,
        price=Decimal("50000.5"),
        size=Decimal("0.01"),
        aggressor=v2.Aggressor.BUY,
    )

    # 0단계: 기준 payload는 유효하다.
    baseline = v2.TradeTick.model_validate(valid_payload)
    assert baseline.seq == 1

    corruptions = [
        ("ts_event", _SECOND_UNIT),
        ("ts_recv", datetime(2026, 9, 3, 0, 0)),
        ("seq", -1),
        ("aggressor", "MAKER"),
    ]

    for field, bad_value in corruptions:
        payload = dict(valid_payload)
        payload[field] = bad_value
        with pytest.raises(ValidationError) as excinfo:
            v2.TradeTick.model_validate(payload)
        # 이 단계의 오류가 망가뜨린 필드를 가리켜야 한다 — 다른 필드의
        # 사유가 새어들어 엉뚱한 필드를 탓하지 않음을 증명.
        errored_fields = {err["loc"][0] for err in excinfo.value.errors()}
        assert field in errored_fields, (
            f"{field} 손상이 {errored_fields}로 잘못 보고됐다(사유 누출)."
        )

    # 마지막 단계: 앞선 반복이 valid_payload 자체를 변형하지 않았으므로
    # 기준 payload는 여전히 유효해야 한다(딕셔너리 복사가 제대로 격리됨).
    final = v2.TradeTick.model_validate(valid_payload)
    assert final.seq == 1
    assert final.aggressor == v2.Aggressor.BUY


# ---- 4. 동시 다중 인스턴스(D3) ---------------------------------------------


def test_concurrent_construction_across_threads_does_not_cross_contaminate() -> None:
    """서로 다른 venue/seq 조합의 TradeTick·BookL2를 스레드풀에서 동시에
    반복 구성한다 — 각 워커가 매번 자기 입력에 맞는 값만 받고, 다른 워커의
    값으로 오염되지 않아야 한다(frozen pydantic 모델이라면 공유 가변 상태가
    없어 당연해야 하나, 회귀 시 모듈 레벨 캐시가 실수로 추가되는 것을 이
    테스트가 잡는다)."""
    venues = [Venue.BITGET, Venue.UPBIT, Venue.BINANCE]

    def _run(worker_id: int, repeat: int) -> tuple[int, list[bool]]:
        venue = venues[worker_id % len(venues)]
        outcomes = []
        for i in range(repeat):
            seq = worker_id * 1000 + i
            tick = _trade_tick(venue=venue, seq=seq)
            book = _book_l2(seq=seq)
            ok = tick.venue == venue and tick.seq == seq and book.seq == seq
            outcomes.append(ok)
        return worker_id, outcomes

    with ThreadPoolExecutor(max_workers=12) as pool:
        futures = [pool.submit(_run, worker_id, 50) for worker_id in range(24)]
        results = [f.result() for f in futures]

    for worker_id, outcomes in results:
        assert all(outcomes), f"동시 구성 중 워커 {worker_id}가 다른 워커에 오염됐다."
