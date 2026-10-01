"""RD-15 (a) -- `adapters/sources/gdelt.py` unit tests.

Spec: docs/specs/L4_research_data_and_market_ecosystem_v1.0.md sec.9 RD-15
DoD ("link_only 강제").
"""

from __future__ import annotations

import time
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from src.foundation.research_data.adapters.sources.gdelt import (
    GDELT_SOURCE_ID,
    GdeltApiError,
    GdeltParseError,
    build_research_item_from_gkg,
    fetch_gdelt_gkg_export,
    parse_gdelt_gkg_export,
    parse_gdelt_gkg_row,
)
from src.foundation.research_data.domain.source_eval_gate import (
    SourceEvalGateError,
    assert_source_eval_gate,
)

_REPO_ROOT = Path(__file__).resolve().parents[4]
_EVAL_PATH = _REPO_ROOT / "docs" / "design" / "RESEARCH_DATA_SOURCE_EVAL.md"

_COLLECTED_AT = datetime(2026, 9, 24, 6, 5, 0, tzinfo=timezone.utc)


def _row(**overrides: str) -> str:
    fields = [
        "20260923060000-123",  # GKGRECORDID
        "20260923060000",  # DATE
        "1",  # SourceCollectionIdentifier
        "example.com",  # SourceCommonName
        "https://example.com/article/123",  # DocumentIdentifier
        "",  # Counts
        "",  # V2Counts
        "",  # Themes
        "ECON_INFLATION;TAX_FNCACT",  # V2Themes
        "",  # Locations
        "",  # V2Locations
        "",  # Persons
        "",  # V2Persons
        "",  # Organizations
        "",  # V2Organizations
        "-2.5,3.1,1.1,0,0,0,10",  # V2Tone
    ]
    for key, value in overrides.items():
        index = {
            "record_id": 0,
            "date": 1,
            "source_common_name": 3,
            "document_url": 4,
            "v2themes": 8,
            "v2tone": 15,
        }[key]
        fields[index] = value
    return "\t".join(fields)


def test_parse_gdelt_gkg_row_parses_expected_fields() -> None:
    record = parse_gdelt_gkg_row(_row())
    assert record.record_id == "20260923060000-123"
    assert record.published_at == datetime(2026, 9, 23, 6, 0, 0, tzinfo=timezone.utc)
    assert record.source_common_name == "example.com"
    assert record.document_url == "https://example.com/article/123"
    assert record.themes == ("ECON_INFLATION", "TAX_FNCACT")
    assert record.tone == -2.5


def test_parse_gdelt_gkg_export_parses_multiple_rows() -> None:
    text = "\n".join([_row(), _row(record_id="20260923060000-124")])
    records = parse_gdelt_gkg_export(text)
    assert len(records) == 2
    assert records[1].record_id == "20260923060000-124"


def test_build_research_item_from_gkg_forces_link_only() -> None:
    record = parse_gdelt_gkg_row(_row())
    item = build_research_item_from_gkg(record, collected_at=_COLLECTED_AT)
    assert item.source_id == GDELT_SOURCE_ID
    assert item.kind == "news"
    assert item.body_ref is None
    assert item.known_at == _COLLECTED_AT
    assert item.url == "https://example.com/article/123"
    assert item.title == "example.com"


def test_build_research_item_from_gkg_is_deterministic() -> None:
    record = parse_gdelt_gkg_row(_row())
    first = build_research_item_from_gkg(record, collected_at=_COLLECTED_AT)
    second = build_research_item_from_gkg(record, collected_at=_COLLECTED_AT)
    assert first.item_id == second.item_id
    assert first.hash == second.hash


# --- negative tests (>=3, D2 floor) ----------------------------------------


def test_parse_gdelt_gkg_row_raises_on_too_few_columns() -> None:
    with pytest.raises(GdeltParseError):
        parse_gdelt_gkg_row("only\tfour\tcolumns\there")


def test_parse_gdelt_gkg_row_raises_on_invalid_date() -> None:
    with pytest.raises(GdeltParseError):
        parse_gdelt_gkg_row(_row(date="not-a-date"))


def test_parse_gdelt_gkg_row_raises_on_missing_record_id() -> None:
    with pytest.raises(GdeltParseError):
        parse_gdelt_gkg_row(_row(record_id=""))


def test_parse_gdelt_gkg_export_raises_on_empty_payload() -> None:
    with pytest.raises(GdeltApiError):
        parse_gdelt_gkg_export("   ")


async def test_fetch_gdelt_gkg_export_denied_when_admission_flips() -> None:
    client = AsyncMock()
    admission_override = {"GDELT": "deny"}
    with patch(
        "src.foundation.research_data.adapters.sources.gdelt.EXPECTED_ADMISSION",
        admission_override,
    ):
        with pytest.raises(GdeltApiError):
            await fetch_gdelt_gkg_export(
                client, "https://example.com/gkg.csv", collected_at=_COLLECTED_AT
            )
    client.get_text.assert_not_called()


# --- failure injection: transport raises -----------------------------------


async def test_fetch_gdelt_gkg_export_propagates_transport_failure() -> None:
    client = AsyncMock()
    client.get_text.side_effect = ConnectionError("GDELT unreachable")
    with pytest.raises(ConnectionError):
        await fetch_gdelt_gkg_export(
            client, "https://example.com/gkg.csv", collected_at=_COLLECTED_AT
        )


async def test_fetch_gdelt_gkg_export_builds_items() -> None:
    client = AsyncMock()
    client.get_text.return_value = _row()
    items = await fetch_gdelt_gkg_export(
        client, "https://example.com/gkg.csv", collected_at=_COLLECTED_AT
    )
    assert len(items) == 1
    assert items[0].body_ref is None


# --- numeric performance assertion (D2 floor, ADR-2026-09-09-C) ------------


@pytest.mark.perf
def test_parse_gdelt_gkg_export_throughput_floor() -> None:
    text = "\n".join(_row(record_id=f"rec-{i}") for i in range(2_000))
    started = time.perf_counter()
    records = parse_gdelt_gkg_export(text)
    elapsed = time.perf_counter() - started
    assert len(records) == 2_000
    throughput = len(records) / elapsed
    assert throughput > 5_000, f"parse_gdelt_gkg_export throughput too low: {throughput:.0f}/s"


# --- gate red reproduction (D2 floor) --------------------------------------


def test_source_eval_gate_fails_on_injected_gdelt_admission_drift() -> None:
    """`fetch_gdelt_gkg_export` trusts the frozen `EXPECTED_ADMISSION[GDELT_SOURCE_ID]
    == "allow"` constant (module docstring) rather than re-reading RD-1's eval doc on
    every call -- the real gate that would catch a silent flip away from that frozen
    value is RD-1's `assert_source_eval_gate` over `RESEARCH_DATA_SOURCE_EVAL.md`
    §7. This reproduces that gate actually going red: if GDELT's §7 conclusion were
    edited to 반입 금지 without the frozen constant changing too, the gate must
    reject the document instead of letting the drift pass silently.
    """
    eval_text = _EVAL_PATH.read_text(encoding="utf-8")
    needle = (
        "**결론: 허용** (뉴스 본문 `link_only` 강제 "
        "— 본문 저장·요약 생성 금지를 RD-3/RD-15가 코드로 강제해야 한다)."
    )
    assert needle in eval_text, "GDELT §7 conclusion text moved -- update this test's needle"
    drifted = eval_text.replace(
        needle, "**결론: 반입 금지** (synthetic drift injected by test).", 1
    )
    with pytest.raises(SourceEvalGateError) as excinfo:
        assert_source_eval_gate(drifted)
    assert excinfo.value.code in {"ADMISSION_DRIFT", "TABLE_SECTION_MISMATCH"}
