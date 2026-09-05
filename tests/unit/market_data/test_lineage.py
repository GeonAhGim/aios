"""LA-8/LA-23b — market_data/domain/lineage.py 순수 규칙 테스트.

Spec: docs/specs/L4_market_data_positions_ledger_v1.0.md#§2.2 LA-8, §9.2 LA-8,
`unit/market_data/test_lineage.py` 표(§9행 570): 순서 다른 같은 레코드 → 같은
해시, 한 값 변경 → 다른 해시.

`test_batch_hash_streaming_matches_reference_property`는
ADR-2026-09-04-A #2(연단위 리플레이 성능) 요구사항 — 스트리밍 재구현
`batch_hash`가 옛 구현 `_batch_hash_reference`와 무작위 배치 200건 이상에서
바이트 단위로 동일한 다이제스트를 내는지 증명한다(저장된 해시 재계산·
backfill 없이 P3 WORM을 지키기 위한 필수 증거, `domain/lineage.py` 모듈
docstring 참고).

task-1136(QA): `_canonical_json`이 재사용 `JSONEncoder`로 바뀐 뒤에도
(1) `json.dumps` 직접 호출(`_canonical_json_reference`)과 문자열이 같고,
(2) 고정 벡터의 다이제스트 값 자체가 변하지 않음(golden)을 고정한다 —
해시 규칙은 `hash_chain`과 공유하는 계약이라 값이 바뀌면 저장된 해시
(P3 WORM)와 어긋나므로, 어떤 "최적화"가 바이트를 바꾸면 여기서 실패해야 한다.
"""
from __future__ import annotations

import random
import time
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from uuid import UUID, uuid4

from src.foundation.market_data.contracts.v1 import CandleRecord, SeriesKey, Timeframe, Venue
from src.foundation.market_data.domain.lineage import (
    _batch_hash_reference,
    _canonical_json,
    _canonical_json_reference,
    batch_hash,
    request_fingerprint,
)


def _candle(open_time: datetime, close: str) -> CandleRecord:
    key = SeriesKey(venue=Venue.KIS_KRX, instrument_id=uuid4(), timeframe=Timeframe.D1)
    return CandleRecord(
        key=key,
        open_time=open_time,
        close_time=open_time,
        open=Decimal(close),
        high=Decimal(close),
        low=Decimal(close),
        close=Decimal(close),
        volume=Decimal("1"),
    )


def test_batch_hash_is_order_independent() -> None:
    a = _candle(datetime(2024, 1, 1, tzinfo=timezone.utc), "100")
    b = _candle(datetime(2024, 1, 2, tzinfo=timezone.utc), "200")

    assert batch_hash([a, b]) == batch_hash([b, a])


def test_batch_hash_changes_when_one_value_changes() -> None:
    a = _candle(datetime(2024, 1, 1, tzinfo=timezone.utc), "100")
    b = _candle(datetime(2024, 1, 2, tzinfo=timezone.utc), "200")
    b_changed = _candle(datetime(2024, 1, 2, tzinfo=timezone.utc), "201")

    assert batch_hash([a, b]) != batch_hash([a, b_changed])


def test_batch_hash_empty_is_stable() -> None:
    assert batch_hash([]) == batch_hash([])


def test_request_fingerprint_is_deterministic_regardless_of_param_order() -> None:
    fp1 = request_fingerprint("kis", {"symbol": "005930", "venue": "KRX"})
    fp2 = request_fingerprint("kis", {"venue": "KRX", "symbol": "005930"})

    assert fp1 == fp2


def test_request_fingerprint_changes_when_param_value_changes() -> None:
    fp1 = request_fingerprint("kis", {"symbol": "005930"})
    fp2 = request_fingerprint("kis", {"symbol": "000660"})

    assert fp1 != fp2


def _random_candle(rng: random.Random, base: datetime) -> CandleRecord:
    key = SeriesKey(venue=Venue.BITGET, instrument_id=uuid4(), timeframe=Timeframe.M1)
    open_time = base + timedelta(minutes=rng.randint(0, 10_000))
    open_ = Decimal(rng.randint(1, 100_000)) / Decimal(100)
    spread = Decimal(rng.randint(0, 500)) / Decimal(100)
    return CandleRecord(
        key=key,
        open_time=open_time,
        close_time=open_time + timedelta(minutes=1),
        open=open_,
        high=open_ + spread,
        low=open_ - spread,
        close=open_ + Decimal(rng.randint(-500, 500)) / Decimal(100),
        volume=Decimal(rng.randint(0, 1_000_000)) / Decimal(1000),
        quote_volume=None if rng.random() < 0.5 else Decimal(rng.randint(0, 1_000_000)),
    )


def test_batch_hash_streaming_matches_reference_property() -> None:
    """ADR-2026-09-04-A #2: 무작위 배치 200건 이상에서 새 스트리밍
    `batch_hash`가 옛 `_batch_hash_reference`와 바이트 단위로 동일하다 —
    저장된 해시를 재계산하지 않고도(P3 WORM) 안전하게 교체할 수 있다는
    증거."""
    rng = random.Random(20260904)
    base = datetime(2026, 1, 1, tzinfo=timezone.utc)

    for _ in range(200):
        size = rng.randint(0, 30)
        records = [_random_candle(rng, base) for _ in range(size)]
        rng.shuffle(records)

        assert batch_hash(records) == _batch_hash_reference(records)


def test_batch_hash_streaming_matches_reference_for_empty_and_singleton() -> None:
    """경계값(음성 테스트 성격): 빈 배치·단일 레코드도 옛 구현과 동일해야
    한다 — property 테스트의 `randint(0, 30)`이 0을 뽑을 확률이 낮아 별도로
    고정한다."""
    assert batch_hash([]) == _batch_hash_reference([])

    rng = random.Random(1)
    solo = [_random_candle(rng, datetime(2026, 1, 1, tzinfo=timezone.utc))]
    assert batch_hash(solo) == _batch_hash_reference(solo)


def test_batch_hash_large_batch_stays_order_independent() -> None:
    """task-1111(esc-ci-8e93e475afa9 후속) 실측: DB 없이 순수 함수만으로도
    `batch_hash` 대량 배치 비용을 관측 가능하게 남긴다. 실측 결과 지배적
    비용은 `_canonical_json`의 레코드별 `model_dump(mode="json")` +
    `json.dumps`이고(`domain/lineage.py` 모듈 docstring,
    `test_perf_replay.py` 모듈 docstring과 동일 결론), 이 규모까지도
    순서무관 불변식은 그대로 유지된다 — 성능 단언은 걸지 않는다(CI 상시
    적색 방지 정책, 3ea1fc1/9bdcd21 선례). 해시 값 자체를 바꾸는 최적화
    (예: `model_dump_json()`)는 시도하지 않았다 — canonical JSON의
    `sort_keys=True` 출력과 바이트가 달라 `hash_version=2` 없이는 저장된
    해시(P3 WORM)와 어긋난다(task-1081 note와 동일 결론)."""
    rng = random.Random(20260904)
    base = datetime(2026, 1, 1, tzinfo=timezone.utc)
    records = [_random_candle(rng, base) for _ in range(20_000)]

    started = time.perf_counter()
    forward = batch_hash(records)
    elapsed = time.perf_counter() - started
    print(
        f"\nbatch_hash latency (n=20000, no DB): {elapsed:.4f}s "
        f"({elapsed / len(records) * 1e6:.2f}us/record)"
    )

    shuffled = list(records)
    rng.shuffle(shuffled)
    assert batch_hash(shuffled) == forward
    # task-1136: 이 규모에서도 옛 구현(json.dumps 직접 호출 + join)과 바이트 동일.
    assert _batch_hash_reference(records) == forward


def _fixed_candle(day: int, close: str) -> CandleRecord:
    key = SeriesKey(
        venue=Venue.KIS_KRX,
        instrument_id=UUID("00000000-0000-0000-0000-000000000001"),
        timeframe=Timeframe.D1,
    )
    at = datetime(2026, 1, day, tzinfo=timezone.utc)
    return CandleRecord(
        key=key, open_time=at, close_time=at, open=Decimal(close), high=Decimal(close),
        low=Decimal(close), close=Decimal(close), volume=Decimal("1"),
    )


def test_batch_hash_golden_vector_is_pinned() -> None:
    """golden(task-1136): 고정 입력의 다이제스트 값을 그대로 고정한다. 값은
    encoder 재사용 이전 구현(commit 9dcb231)으로 계산했다 — canonical JSON
    규칙(정렬 키·`", "`/`": "` 구분자·`default=str`·ensure_ascii)이나
    `CandleRecord`의 직렬화 필드가 하나라도 바뀌면 실패한다(계약 변경 감지)."""
    a, b = _fixed_candle(1, "100.5"), _fixed_candle(2, "200")

    assert _canonical_json(a) == (
        '{"close": "100.5", "close_time": "2026-01-01T00:00:00Z", "high": "100.5", '
        '"key": {"instrument_id": "00000000-0000-0000-0000-000000000001", '
        '"schema_version": "v1", "timeframe": "1d", "venue": "KIS_KRX"}, '
        '"low": "100.5", "open": "100.5", "open_time": "2026-01-01T00:00:00Z", '
        '"quote_volume": null, "schema_version": "v1", "volume": "1"}'
    )
    assert batch_hash([a, b]) == (
        "694bd724f4e50219ac6ead1b7e1c2d42e125f8b9e8a120727731787edfa42b06"
    )
    assert request_fingerprint("kis", {"symbol": "005930", "venue": "KRX"}) == (
        "13ac4875197839f40af5e67f4174ed266c13f86f090dee57194c913c0ff19c7b"
    )


def test_canonical_json_matches_json_dumps_reference() -> None:
    """task-1136: 재사용 encoder 경로가 `json.dumps(..., sort_keys=True,
    default=str)` 직접 호출과 문자열 단위로 같다 — 모델뿐 아니라
    `request_fingerprint`가 넘기는 평범한 dict(중첩·Decimal·None·datetime·UUID·
    비ASCII 키·키 정렬)도 포함한다."""
    rng = random.Random(1136)
    base = datetime(2026, 1, 1, tzinfo=timezone.utc)
    for _ in range(100):
        record = _random_candle(rng, base)
        assert _canonical_json(record) == _canonical_json_reference(record)

    plain = {
        "z": Decimal("1.50"), "a": None, "m": {"y": [1, 2.5, "x"], "b": base},
        "한글": "값", "id": uuid4(), "nested": {"d": Decimal("-0.001")},
    }
    assert _canonical_json(plain) == _canonical_json_reference(plain)
    assert _canonical_json([]) == _canonical_json_reference([]) == "[]"
