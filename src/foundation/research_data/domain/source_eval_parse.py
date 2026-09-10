"""RD-1 — RESEARCH_DATA_SOURCE_EVAL.md parse layer (pure, I/O-free).

Extracts LAYER_A section bodies and §9.1/§9.2 tables into typed records.
Invariant checks live in `source_eval_gate.py` (same leaf, split for P6
line_cap). Fail-closed via `SourceEvalGateError`.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

__all__ = [
    "LAYER_A_SOURCE_IDS",
    "EXPECTED_ADMISSION",
    "SourceEvalGateError",
    "SourceEvalRecord",
    "SourceEvalReport",
    "parse_source_eval_markdown",
]

Admission = Literal["allow", "deny"]
RateLimitStatus = Literal["measured", "unconfirmed", "absent_documented", "per_feed"]

LAYER_A_SOURCE_IDS: tuple[str, ...] = (
    "OpenDART",
    "ECOS",
    "KOSIS",
    "KRX",
    "FRED",
    "SEC EDGAR",
    "GDELT",
    "RSS/Atom",
)

# Frozen snapshot of RESEARCH_DATA_SOURCE_EVAL.md §9.1/§9.2 (2026-09-09).
EXPECTED_ADMISSION: dict[str, Admission] = {
    "OpenDART": "allow",
    "ECOS": "deny",
    "KOSIS": "deny",
    "KRX": "deny",
    "FRED": "allow",
    "SEC EDGAR": "allow",
    "GDELT": "allow",
    "RSS/Atom": "allow",
}

_SECTION_HEADER_RE = re.compile(r"^##\s+(\d+)\.\s+(.+?)\s*$", re.MULTILINE)
_URL_RE = re.compile(r"https?://[^\s)>\]\"']+")
_DATE_RE = re.compile(r"20\d{2}-\d{2}-\d{2}")
_BLOCKQUOTE_RE = re.compile(r"^>", re.MULTILINE)
_REDISTRIBUTION_TOKEN_RE = re.compile(
    r"`?(store_full|store_excerpt|link_only)`?",
    re.IGNORECASE,
)
_TITLE_TO_ID: tuple[tuple[str, str], ...] = (
    ("opendart", "OpenDART"),
    ("ecos", "ECOS"),
    ("kosis", "KOSIS"),
    ("krx", "KRX"),
    ("fred", "FRED"),
    ("sec edgar", "SEC EDGAR"),
    ("edgar", "SEC EDGAR"),
    ("gdelt", "GDELT"),
    ("rss", "RSS/Atom"),
    ("atom", "RSS/Atom"),
)
_MEASURED_RATE_RE = re.compile(
    r"("
    r"(\d[\d,]*)\s*(건|회|requests?)"
    r"|"
    r"(일|분|초|per\s+minute|per\s+second|per\s+day)\s*당?\s*(\d[\d,]*)\s*(건|회)?"
    r"|"
    r"(\d[\d,]*)\s*requests?\s+per\s+(minute|second|day)"
    r")",
    re.I,
)


class SourceEvalGateError(ValueError):
    """RD-1 DoD 위반 — 문서 게이트 적색. 추정·완화 없이 즉시 거부."""

    def __init__(self, code: str, detail: str) -> None:
        self.code = code
        self.detail = detail
        super().__init__(f"{code}: {detail}")


@dataclass(frozen=True, slots=True)
class SourceEvalRecord:
    source_id: str
    admission: Admission
    redistribution_tokens: frozenset[str]
    rate_limit_status: RateLimitStatus
    has_blockquote: bool
    has_url: bool
    has_check_date: bool


@dataclass(frozen=True, slots=True)
class SourceEvalReport:
    records: tuple[SourceEvalRecord, ...]
    allow_table_ids: frozenset[str]
    deny_table_ids: frozenset[str]


def _canonical_source_id(title: str) -> str | None:
    lowered = title.strip().lower()
    for needle, source_id in _TITLE_TO_ID:
        if needle in lowered:
            return source_id
    return None


def _split_numbered_sections(markdown: str) -> dict[str, str]:
    matches = list(_SECTION_HEADER_RE.finditer(markdown))
    bodies: dict[str, str] = {}
    for idx, match in enumerate(matches):
        number = int(match.group(1))
        title = match.group(2)
        if number < 1 or number > 8:
            continue
        source_id = _canonical_source_id(title)
        if source_id is None:
            raise SourceEvalGateError(
                "UNKNOWN_SECTION",
                f"section {number} title {title!r} is not a LAYER_A source",
            )
        start = match.end()
        end = matches[idx + 1].start() if idx + 1 < len(matches) else len(markdown)
        if source_id in bodies:
            raise SourceEvalGateError(
                "DUPLICATE_SECTION",
                f"source {source_id} appears more than once",
            )
        bodies[source_id] = markdown[start:end]
    return bodies


def _admission_from_body(body: str) -> Admission:
    if re.search(r"\*\*결론:\s*허용\*\*", body):
        return "allow"
    if re.search(r"\*\*결론:\s*반입 금지", body):
        return "deny"
    if "반입 금지" in body and "결론" in body:
        return "deny"
    raise SourceEvalGateError(
        "MISSING_ADMISSION",
        "section has no '**결론: 허용**' / '**결론: 반입 금지**' line",
    )


def _rate_limit_status(body: str, source_id: str) -> RateLimitStatus:
    if source_id == "RSS/Atom":
        if "일괄 수치 없음" in body or "사이트별 상이" in body:
            return "per_feed"
        raise SourceEvalGateError(
            "RSS_RATE_LIMIT_SHAPE",
            "RSS/Atom must document per-feed / no-global-limit shape",
        )
    gdelt_absent = source_id == "GDELT" and (
        "조항 자체가 없" in body or "공식 rate limit 조항 없음" in body
    )
    if "미확인" in body and re.search(r"rate limit|요청 제한|호출", body, re.I):
        if gdelt_absent:
            return "absent_documented"
        return "unconfirmed"
    if gdelt_absent:
        return "absent_documented"
    if _MEASURED_RATE_RE.search(body):
        return "measured"
    if "미확인" in body:
        return "unconfirmed"
    raise SourceEvalGateError(
        "RATE_LIMIT_UNPARSED",
        f"{source_id}: neither measured numeric bound nor 미확인 found",
    )


def _redistribution_tokens(body: str) -> frozenset[str]:
    tokens = {m.group(1).lower() for m in _REDISTRIBUTION_TOKEN_RE.finditer(body)}
    if not tokens and "반입 금지" not in body:
        raise SourceEvalGateError(
            "MISSING_REDISTRIBUTION",
            "no store_full|store_excerpt|link_only token and not 반입 금지",
        )
    return frozenset(tokens)


def _parse_conclusion_tables(markdown: str) -> tuple[frozenset[str], frozenset[str]]:
    allow: set[str] = set()
    deny: set[str] = set()
    in_allow = False
    in_deny = False
    for line in markdown.splitlines():
        if line.startswith("### 9.1"):
            in_allow, in_deny = True, False
            continue
        if line.startswith("### 9.2"):
            in_allow, in_deny = False, True
            continue
        if line.startswith("### 9.3") or line.startswith("## "):
            in_allow = in_deny = False
            continue
        if not line.startswith("|"):
            continue
        cells = [c.strip() for c in line.strip("|").split("|")]
        if not cells or cells[0] in {"소스", "---", ""}:
            continue
        source_id = _canonical_source_id(cells[0])
        if source_id is None:
            continue
        if in_allow:
            allow.add(source_id)
        elif in_deny:
            deny.add(source_id)
    return frozenset(allow), frozenset(deny)


def parse_source_eval_markdown(markdown: str) -> SourceEvalReport:
    """Parse LAYER_A sections + §9 tables. Raises on structural failure."""
    if not markdown or not markdown.strip():
        raise SourceEvalGateError("EMPTY_DOCUMENT", "markdown is empty")

    bodies = _split_numbered_sections(markdown)
    missing = [s for s in LAYER_A_SOURCE_IDS if s not in bodies]
    if missing:
        raise SourceEvalGateError(
            "MISSING_SOURCES",
            f"LAYER_A sources absent from numbered sections: {missing}",
        )

    records: list[SourceEvalRecord] = []
    for source_id in LAYER_A_SOURCE_IDS:
        body = bodies[source_id]
        records.append(
            SourceEvalRecord(
                source_id=source_id,
                admission=_admission_from_body(body),
                redistribution_tokens=_redistribution_tokens(body),
                rate_limit_status=_rate_limit_status(body, source_id),
                has_blockquote=bool(_BLOCKQUOTE_RE.search(body)),
                has_url=bool(_URL_RE.search(body)),
                has_check_date=bool(_DATE_RE.search(body)),
            )
        )

    allow_ids, deny_ids = _parse_conclusion_tables(markdown)
    return SourceEvalReport(
        records=tuple(records),
        allow_table_ids=allow_ids,
        deny_table_ids=deny_ids,
    )
