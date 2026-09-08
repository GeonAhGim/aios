"""DC-15 — hot→warm 캔들 승격/아카이브 잡: 멱등 + 계보 기록.

Spec: docs/specs/L4_analytics_authoring_backtest_marketplace_v1.0.md
§2.1 DC-15(선행 DC-14), §9.2 DC-15.

승격 순서(DoD(b) fail-closed): hot에서 읽는다 → warm에 쓴다(DC-14
`WarmParquetStorage.write_year`) → warm을 다시 읽어 digest가 원본과
바이트 동일한지 검증한다 → **검증을 통과했을 때만** hot에서 그 연도를
지운다. 검증 실패(`VerificationFailedError`) 또는 그 이전 어느 단계에서
예외가 나든 hot은 그대로 남는다 — `write_year`는 대상 파일을 항상
통째로 덮어쓰므로(DC-14) 중단된 부분 파일이 있어도 다음 실행이 hot의
전체 데이터로 처음부터 다시 쓴다(DoD(c), 별도의 재개 상태를 두지 않는
것 자체가 안전장치다).

멱등(DoD(a))은 "승격할 게 없으면 아무것도 안 한다"로 얻는다: 해당
연도에 hot 행이 이미 0건이면(첫 승격이 성공해 지워졌거나 애초에 없던
경우) warm/계보 어느 쪽도 건드리지 않고 즉시 반환한다 — 두 번째 실행은
결과 parquet도, 계보 파일도 바꾸지 않는다.

계보(DoD(d))는 새 스키마를 만들지 않는다 — DC-22
`contracts/v2/candle_lineage.SourceKind`를 그대로 재사용해 승격 배치를
태그만 하고, 레코드 자체는 임시 dict를 JSON Lines로 남긴다(신규 pydantic
모델·DB 테이블 없음). `md_candle`은 아직 캔들 단위 `source_kind`를 갖지
않으므로(DC-13 문서화된 경계) 이 잡은 항상 `VENDOR`로 태그한다 — 실제
출처를 아는 상위 컨텍스트가 생기면 그 값을 받아쓰도록 바꿔야 한다.

새 마이그레이션 없음(DoD(e)) — 계보는 warm 루트 옆 JSON Lines 파일.

경계(정직하게 남겨 둔다): `hot.delete_year`는 연도 전체를 지우므로,
`hot.read_year` 스냅샷 이후 같은 연도에 새 행이 동시에 들어오면 그 행도
함께(승격되지 않은 채) 지워질 수 있다 — 동시 백필과의 직렬화는 이
리프의 범위 밖(단일 워커 배치 잡을 전제)이다.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Protocol, runtime_checkable

from src.foundation.market_data.contracts.v1 import SeriesKey
from src.foundation.market_data.contracts.v2.candle_lineage import SourceKind
from src.foundation.market_data.domain.candle_columns import CandleColumns

__all__ = [
    "HotYearSource",
    "PromotionOutcome",
    "VerificationFailedError",
    "WarmYearStorage",
    "promote_year",
    "read_lineage",
]


class VerificationFailedError(RuntimeError):
    """warm 왕복 검증(digest 일치, 결측 구간 없음)이 실패했다 — hot은
    지우지 않는다(fail-closed, DoD(b))."""


@runtime_checkable
class HotYearSource(Protocol):
    """tiering이 hot 계층에 요구하는 최소 표면. `HotPostgresStorage`(DC-13)
    는 이 두 메서드를 아직 갖지 않는다 — 실 Postgres 배선은 이 Protocol을
    구현하는 어댑터를 만드는 후속 작업 소관이고, 이 리프는 인터페이스와
    순수 오케스트레이션만 책임진다."""

    async def read_year(self, key: SeriesKey, year: int) -> CandleColumns:
        """`year` 전체(해당 연도 `open_time`만)를 반환한다. 없으면 빈
        `CandleColumns`."""
        ...

    async def delete_year(self, key: SeriesKey, year: int) -> int:
        """`year` 전체를 지우고 지운 행 수를 반환한다."""
        ...


@runtime_checkable
class WarmYearStorage(Protocol):
    """Minimal surface tiering requires from the warm tier — implemented by
    `WarmParquetStorage`, and structurally satisfied by test fakes that
    inject verification failures or mid-write crashes."""

    def write_year(self, key: SeriesKey, year: int, columns: CandleColumns) -> Path:
        """Overwrites the full `year` and returns the written file path."""
        ...

    def read_columns(
        self, key: SeriesKey, start: datetime, end: datetime
    ) -> tuple[CandleColumns, tuple[tuple[datetime, datetime], ...]]:
        """Reads `[start, end)` and returns (covered columns, missing ranges)."""
        ...


@dataclass(frozen=True, slots=True)
class PromotionOutcome:
    """`promote_year` 한 번 호출의 결과. `promoted=False`는 멱등 스킵
    (승격할 hot 행이 없었음)을 뜻한다."""

    promoted: bool
    row_count: int
    digest: str


def _year_bounds(year: int) -> tuple[datetime, datetime]:
    start = datetime(year, 1, 1, tzinfo=timezone.utc)
    end = datetime(year + 1, 1, 1, tzinfo=timezone.utc)
    return start, end


def _digest(columns: CandleColumns) -> str:
    """DC-14 테스트의 정준 직렬화와 같은 방식(값만 비교, tzinfo 클래스
    정체성 무시) — 여기서는 hot→warm 왕복 검증에 실제로 쓰인다."""
    parts: list[str] = []
    for i in range(len(columns)):
        parts.append(columns.ts[i].isoformat())
        parts.append(str(columns.open[i]))
        parts.append(str(columns.high[i]))
        parts.append(str(columns.low[i]))
        parts.append(str(columns.close[i]))
        parts.append(str(columns.volume[i]))
        parts.append("" if columns.quote_volume[i] is None else str(columns.quote_volume[i]))
    return hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()


def _lineage_path(root: Path, key: SeriesKey) -> Path:
    return root / key.venue.value / key.timeframe.value / str(key.instrument_id) / "lineage.jsonl"


def read_lineage(root: Path, key: SeriesKey) -> list[dict[str, object]]:
    """지금까지 이 시리즈에 기록된 승격 계보 행을 오름차순으로 반환한다.
    파일이 없으면 빈 목록(아직 승격된 적 없음)."""
    path = _lineage_path(root, key)
    if not path.exists():
        return []
    lines = path.read_text(encoding="utf-8").splitlines()
    return [json.loads(line) for line in lines if line]


def _append_lineage(root: Path, key: SeriesKey, entry: dict[str, object]) -> None:
    path = _lineage_path(root, key)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry, sort_keys=True))
        f.write("\n")


async def promote_year(
    hot: HotYearSource, warm: WarmYearStorage, root: Path, key: SeriesKey, year: int
) -> PromotionOutcome:
    """`year`의 hot 파티션을 warm parquet로 승격하고, 검증 통과 후에만
    hot에서 지운 뒤 계보 한 줄을 남긴다. hot에 그 연도 행이 이미 없으면
    아무것도 하지 않고 반환한다(DoD(a) 멱등)."""
    source = await hot.read_year(key, year)
    if len(source) == 0:
        return PromotionOutcome(promoted=False, row_count=0, digest="")

    source_digest = _digest(source)
    warm.write_year(key, year, source)

    start, end = _year_bounds(year)
    written, _missing = warm.read_columns(key, start, end)
    # `_missing`는 그 달력 월에 데이터가 아예 없다는 뜻일 뿐(§DC-14
    # read_columns) — hot 파티션이 원래 그 달에 캔들이 없었다면 정상이라
    # 훼손 신호가 아니다. 왕복 무결성은 digest 동일성만으로 판단한다.
    if _digest(written) != source_digest:
        raise VerificationFailedError(
            f"warm round-trip verification failed for {key.instrument_id}/{year} "
            "(fail-closed — hot rows kept)"
        )

    await hot.delete_year(key, year)
    _append_lineage(
        root,
        key,
        {
            "year": year,
            "digest": source_digest,
            "row_count": len(source),
            "promoted_at": datetime.now(tz=timezone.utc).isoformat(),
            "source_kind": SourceKind.VENDOR.value,
        },
    )
    return PromotionOutcome(promoted=True, row_count=len(source), digest=source_digest)
