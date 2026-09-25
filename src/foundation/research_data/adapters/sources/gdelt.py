# ratchet-allow: GDELT GKG 2.1 export column layout (GKGRECORDID..V2Tone) is
# cited from the publicly circulated GKG codebook, not re-verified against a
# live daily export file in this session. GKG never carries an article
# headline field (only extracted entities), so `title` falls back to
# `SourceCommonName` -- a documented limitation, not a guess -- and every
# parse failure raises `GdeltParseError` instead of silently skipping a row.
"""RD-15 (a) -- adapters/sources/gdelt.py: GDELT Global Knowledge Graph (GKG)
event-metadata collector adapter.

Spec: docs/specs/L4_research_data_and_market_ecosystem_v1.0.md sec.9 RD-15
("link_only 강제, 사용자 RSS 등록"); RD-1
docs/design/RESEARCH_DATA_SOURCE_EVAL.md sec.7 GDELT.

RD-1 sec.7(b) draws a hard line this module enforces structurally, not just
by convention: GDELT's own structured event/GKG metadata may be
`store_full`, but GDELT does not own the underlying news article -- it is a
third party's (the publisher's) copyrighted work -- so GDELT's permissive
license cannot extend to it. `build_research_item_from_gkg` therefore never
accepts a body/summary parameter at all; every `ResearchItem` it produces
has `body_ref=None` by construction, so there is no code path that could
accidentally store article text.

`known_at` is the caller-supplied `collected_at`, never the GKG `DATE`
field -- the system could not have known about a row before actually
fetching the export file that contains it (RD-A1 point-in-time integrity),
and GDELT's own publish delay (rows are batched into a file "매일 06:00 EST
갱신 게시", RD-1 sec.7(c)) means `DATE` alone would understate that lag.

This module only parses an already-downloaded GKG export payload --
retrieving and decompressing the daily zip is the caller's I/O detail and is
not guessed at here (unverified specifics about GDELT's file transport are
out of this leaf's scope).
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Protocol
from uuid import UUID, uuid5

from src.foundation.research_data.contracts.v1 import ResearchItem, SourceMeta
from src.foundation.research_data.domain.source_eval_gate import EXPECTED_ADMISSION

__all__ = [
    "GDELT_SOURCE_ID",
    "GDELT_SOURCE_META",
    "GdeltApiError",
    "GdeltParseError",
    "GdeltHttpClient",
    "GdeltGkgRecord",
    "parse_gdelt_gkg_row",
    "parse_gdelt_gkg_export",
    "build_research_item_from_gkg",
    "fetch_gdelt_gkg_export",
]

GDELT_SOURCE_ID = "GDELT"

# GKG 2.1's own field order -- see module-level ratchet-allow note.
_COL_RECORD_ID = 0
_COL_DATE = 1
_COL_SOURCE_COMMON_NAME = 3
_COL_DOCUMENT_IDENTIFIER = 4
_COL_V2THEMES = 8
_COL_V2TONE = 15
_MIN_COLUMNS = _COL_V2TONE + 1

# uuid5 namespace fixed to this module so `item_id` is deterministic per
# `(source_id, GKGRECORDID)` -- re-parsing the same export row never
# produces a different `item_id`, matching RD-4's re-collection idempotency
# ("re-collection is the same row").
_ITEM_ID_NAMESPACE = UUID("6f2c9f1e-6b8a-4f3a-9c2d-2a1c9e5b7d10")

# RD-1 sec.7(b): the article this item merely points at is never GDELT's to
# redistribute -- `link_only` is not a runtime choice, it is this source's
# fixed legal posture. Callers register this via
# `postgres_repository.upsert_source` before ingesting.
GDELT_SOURCE_META = SourceMeta(
    source_id=GDELT_SOURCE_ID,
    publisher="The GDELT Project",
    redistribution="link_only",
    license_ref="docs/design/RESEARCH_DATA_SOURCE_EVAL.md#7-gdelt",
    rate_limit=0,  # RD-1 sec.7(c): static file distribution, no documented request cap.
    coverage="GKG 2.1 daily export, ~06:00 EST publish, no auth key required",
)


class GdeltApiError(RuntimeError):
    """GDELT's RD-1 admission flipped to `deny`, or the caller passed an
    empty export payload where a real one was expected -- fail-closed, not
    a silent empty result."""


class GdeltParseError(ValueError):
    """A GKG row had fewer than `_MIN_COLUMNS` tab-separated fields, or its
    `DATE`/`V2Tone` could not be parsed -- an unrecognized shape is a parse
    failure, never guessed past."""


class GdeltHttpClient(Protocol):
    """Injected transport -- this module never fetches or decompresses the
    GKG export itself, so a unit test can supply a fixture string without
    any network access."""

    async def get_text(self, url: str) -> str: ...


@dataclass(frozen=True)
class GdeltGkgRecord:
    record_id: str
    published_at: datetime
    source_common_name: str
    document_url: str
    themes: tuple[str, ...]
    tone: float | None


def _parse_gkg_date(raw: str) -> datetime:
    try:
        return datetime.strptime(raw, "%Y%m%d%H%M%S").replace(tzinfo=timezone.utc)
    except ValueError as exc:
        raise GdeltParseError(f"GKG DATE is not YYYYMMDDHHMMSS: {raw!r}") from exc


def _parse_tone(raw: str) -> float | None:
    if not raw.strip():
        return None
    first_field = raw.split(",", 1)[0].strip()
    if not first_field:
        return None
    try:
        return float(first_field)
    except ValueError as exc:
        raise GdeltParseError(f"GKG V2Tone leading field is not numeric: {raw!r}") from exc


def parse_gdelt_gkg_row(line: str) -> GdeltGkgRecord:
    """Parse one tab-separated GKG export row. Raises `GdeltParseError` for
    any row that does not have at least `_MIN_COLUMNS` fields or whose
    `DATE`/`V2Tone` cannot be parsed."""
    columns = line.rstrip("\n").rstrip("\r").split("\t")
    if len(columns) < _MIN_COLUMNS:
        raise GdeltParseError(
            f"GKG row has {len(columns)} columns, expected at least {_MIN_COLUMNS}"
        )
    record_id = columns[_COL_RECORD_ID].strip()
    document_url = columns[_COL_DOCUMENT_IDENTIFIER].strip()
    if not record_id or not document_url:
        raise GdeltParseError("GKG row missing GKGRECORDID or DocumentIdentifier")
    raw_themes = columns[_COL_V2THEMES].strip()
    themes = tuple(t for t in raw_themes.split(";") if t) if raw_themes else ()
    return GdeltGkgRecord(
        record_id=record_id,
        published_at=_parse_gkg_date(columns[_COL_DATE].strip()),
        source_common_name=columns[_COL_SOURCE_COMMON_NAME].strip(),
        document_url=document_url,
        themes=themes,
        tone=_parse_tone(columns[_COL_V2TONE]),
    )


def parse_gdelt_gkg_export(text: str) -> list[GdeltGkgRecord]:
    """Parse a full GKG export payload (one row per line). A single
    malformed row fails the whole call (fail-closed) rather than silently
    dropping it -- a partially-ingested daily export is worse than a
    visible, retryable failure."""
    if not text.strip():
        raise GdeltApiError("GDELT GKG export payload is empty")
    return [parse_gdelt_gkg_row(line) for line in text.splitlines() if line.strip()]


def build_research_item_from_gkg(
    record: GdeltGkgRecord, *, collected_at: datetime
) -> ResearchItem:
    """Build a `ResearchItem` for one GKG record. `body_ref` is always
    `None` -- there is no parameter through which a caller could set it,
    which is the point (RD-1 sec.7(b), link_only enforced structurally).

    `known_at=collected_at`, never `record.published_at` -- see module
    docstring on RD-A1 point-in-time integrity.
    """
    item_id = uuid5(_ITEM_ID_NAMESPACE, f"{GDELT_SOURCE_ID}:{record.record_id}")
    title = record.source_common_name or record.document_url
    return ResearchItem(
        item_id=item_id,
        source_id=GDELT_SOURCE_ID,
        kind="news",
        published_at=record.published_at,
        known_at=collected_at,
        instruments=(),
        title=title,
        body_ref=None,
        url=record.document_url,
        language="und",
        hash=hashlib.sha256(record.record_id.encode()).hexdigest(),
        revision_of=None,
    )


async def fetch_gdelt_gkg_export(
    client: GdeltHttpClient, url: str, *, collected_at: datetime
) -> list[ResearchItem]:
    """Fetch and parse one GKG export file into `link_only` `ResearchItem`s.

    RD-1's `RESEARCH_DATA_SOURCE_EVAL.md` records GDELT's admission as
    `allow` -- `check_research_data_source_eval.py`/`source_eval_gate.py`
    enforce that this can never silently flip to `deny` without the
    document itself changing. This call refuses to reach the network if
    that admission ever does flip, rather than caveat it after the fact.
    """
    if EXPECTED_ADMISSION[GDELT_SOURCE_ID] != "allow":
        raise GdeltApiError(
            "GDELT is denied by RD-1 RESEARCH_DATA_SOURCE_EVAL.md -- "
            "ingestion stays disabled until that admission flips back to allow"
        )
    text = await client.get_text(url)
    records = parse_gdelt_gkg_export(text)
    return [build_research_item_from_gkg(r, collected_at=collected_at) for r in records]
