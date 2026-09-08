"""RD-2 — research_data/contracts/v1 스냅샷 + 검증 테스트.

Spec: docs/specs/L4_research_data_and_market_ecosystem_v1.0.md §2.1 RD-2, §3.

`fixtures/research_data_contracts_v1.json`은 현재 스키마의 스냅샷이다.
필드를 지우거나 이름을 바꾸면 이 테스트가 즉시 실패한다(107번 §8 "필드
제거 시 실패"). 필드 추가는 minor 변경이므로 허용되고, 그 경우에만
fixture를 함께 갱신한다.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

import pytest
from pydantic import ValidationError

from src.foundation.research_data.contracts import v1

FIXTURE = Path(__file__).parent / "fixtures" / "research_data_contracts_v1.json"

_MODELS = (v1.ResearchItem, v1.SourceMeta)

_ERROR_CODES = {
    "RD_POINT_IN_TIME_VIOLATION",
    "RD_REDISTRIBUTION_DENIED",
    "RD_SOURCE_UNAVAILABLE",
    "RD_RATE_LIMITED",
}


def _now() -> datetime:
    return datetime(2026, 9, 8, 0, 0, tzinfo=timezone.utc)


def _sample_item(**overrides: object) -> v1.ResearchItem:
    base: dict[str, object] = dict(
        item_id=uuid4(),
        source_id="opendart",
        kind="filing",
        published_at=_now(),
        known_at=_now(),
        instruments=("005930",),
        title="분기보고서",
        body_ref=None,
        url="https://dart.fss.or.kr/x",
        language="ko",
        hash="h" * 64,
        revision_of=None,
    )
    base.update(overrides)
    return v1.ResearchItem(**base)  # type: ignore[arg-type]


def _sample_source_meta(**overrides: object) -> v1.SourceMeta:
    base: dict[str, object] = dict(
        source_id="opendart",
        publisher="금융감독원",
        redistribution="store_full",
        license_ref="opendart-tos-2026",
        rate_limit=1000,
        coverage="2015-01-01~present",
    )
    base.update(overrides)
    return v1.SourceMeta(**base)  # type: ignore[arg-type]


def test_schema_snapshot_matches_fixture() -> None:
    current = {m.__name__: m.model_json_schema() for m in _MODELS}
    expected = json.loads(FIXTURE.read_text(encoding="utf-8"))
    assert current == expected


def test_research_data_error_code_has_exactly_the_taxonomy_from_spec() -> None:
    assert {code.value for code in v1.ResearchDataErrorCode} == _ERROR_CODES


def test_research_item_accepts_aware_datetimes() -> None:
    item = _sample_item()
    assert item.known_at.tzinfo is not None
    assert item.published_at.tzinfo is not None
    assert item.schema_version == "v1"


def test_research_item_naive_known_at_rejected() -> None:
    with pytest.raises(ValidationError):
        _sample_item(known_at=datetime(2026, 9, 8, 0, 0))


def test_research_item_naive_published_at_rejected() -> None:
    with pytest.raises(ValidationError):
        _sample_item(published_at=datetime(2026, 9, 8, 0, 0))


def test_research_item_missing_known_at_rejected() -> None:
    """§3 "known_at 없는 항목은 저장 거부" — 기본값 채우기(now())는 반려 대상이라
    known_at을 아예 빼면 pydantic이 필수 필드 누락으로 막아야 한다."""
    payload = dict(
        item_id=uuid4(),
        source_id="opendart",
        kind="filing",
        published_at=_now(),
        instruments=("005930",),
        title="분기보고서",
        body_ref=None,
        url="https://dart.fss.or.kr/x",
        language="ko",
        hash="h" * 64,
        revision_of=None,
    )
    with pytest.raises(ValidationError) as exc_info:
        v1.ResearchItem(**payload)  # type: ignore[arg-type]
    assert "known_at" in str(exc_info.value)


def test_source_meta_link_only_roundtrip() -> None:
    meta = _sample_source_meta(redistribution="link_only")
    assert meta.redistribution == "link_only"
    assert meta.schema_version == "v1"
