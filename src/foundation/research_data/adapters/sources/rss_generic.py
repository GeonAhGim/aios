"""RD-15 (b) -- adapters/sources/rss_generic.py: generic user-registered
RSS 2.0 / Atom feed collector adapter.

Spec: docs/specs/L4_research_data_and_market_ecosystem_v1.0.md sec.9 RD-15
("link_only 강제, 사용자 RSS 등록"); RD-1
docs/design/RESEARCH_DATA_SOURCE_EVAL.md sec.8 범용 RSS/Atom.

RD-1 sec.8(b) fixes this whole source group's redistribution policy at
`link_only`, with no per-feed exception: neither RSS 2.0 nor Atom's
`atom:rights` element establishes a machine-trustworthy content license
(RFC 4287 explicitly excludes it from that role), so an individual feed's
claimed license is never trusted to promote it to `store_full`/
`store_excerpt`. `build_research_item_from_entry` therefore never accepts a
body/summary parameter -- every `ResearchItem` it produces has
`body_ref=None` by construction, the same structural guarantee as
`gdelt.py`'s.

`RssFeedRegistration` is the user-registration record this leaf's spec
calls out ("사용자 RSS 등록") -- a user adds an arbitrary feed URL, and this
module treats every registered feed identically (protocol-level judgment,
not a per-publisher one, RD-1 sec.8(b)).

RD-1 sec.8(c) notes there is no common rate limit across this source group
-- it must be measured per feed from the feed's own HTTP response
(`429`/`Retry-After`). `fetch_rss_feed` surfaces that as `RssRateLimited`
rather than guessing a shared number.
"""
from __future__ import annotations

import hashlib
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Protocol
from uuid import UUID, uuid5
from xml.etree.ElementTree import Element, ParseError

from defusedxml.ElementTree import fromstring

from src.foundation.research_data.contracts.v1 import ResearchItem, SourceMeta

__all__ = [
    "RSS_GENERIC_SOURCE_ID",
    "RSS_GENERIC_SOURCE_META",
    "RssApiError",
    "RssParseError",
    "RssRateLimited",
    "RssHttpResponse",
    "RssHttpClient",
    "RssFeedRegistration",
    "RssFeedEntry",
    "parse_rss_feed",
    "build_research_item_from_entry",
    "fetch_rss_feed",
]

RSS_GENERIC_SOURCE_ID = "RSS/Atom"

_ATOM_NS = "{http://www.w3.org/2005/Atom}"

# uuid5 namespace fixed to this module -- same determinism rationale as
# `gdelt.py`'s `_ITEM_ID_NAMESPACE`, keyed on `(feed_id, entry guid/link)`.
_ITEM_ID_NAMESPACE = UUID("9b1a2f4c-7d3e-4a5b-8c6f-1e2d3c4b5a69")

# RD-1 sec.8(b): protocol-level fail-closed default -- no registered feed
# ever earns `store_full`/`store_excerpt`, regardless of its own
# `atom:rights` claim. Callers register this via
# `postgres_repository.upsert_source` before ingesting.
RSS_GENERIC_SOURCE_META = SourceMeta(
    source_id=RSS_GENERIC_SOURCE_ID,
    publisher="user-registered feed (protocol-level, no single publisher)",
    redistribution="link_only",
    license_ref="docs/design/RESEARCH_DATA_SOURCE_EVAL.md#8-범용-rssatom",
    rate_limit=0,  # RD-1 sec.8(c): no shared bound -- measured per feed, not declared here.
    coverage="per-feed, user-registered",
)


class RssApiError(RuntimeError):
    """The feed registration was invalid, or the transport returned a
    non-2xx status this module does not otherwise classify -- fail-closed,
    not a silent empty result."""


class RssParseError(ValueError):
    """Feed body was not well-formed XML, or matched neither the RSS 2.0
    (`<rss><channel><item>`) nor the Atom (`<feed><entry>`) shape."""


class RssRateLimited(RuntimeError):
    """The feed responded `429` -- RD-1 sec.8(c): rate limiting is measured
    per feed, not assumed. Carries `retry_after_seconds` when the feed sent
    a `Retry-After` header (`None` if it did not)."""

    def __init__(self, feed_id: str, *, retry_after_seconds: float | None) -> None:
        self.feed_id = feed_id
        self.retry_after_seconds = retry_after_seconds
        super().__init__(f"RSS feed {feed_id!r} rate-limited (429)")


@dataclass(frozen=True)
class RssHttpResponse:
    status_code: int
    headers: Mapping[str, str]
    text: str


class RssHttpClient(Protocol):
    """Injected transport -- this module never opens a socket itself, so a
    unit test can supply a fixture response without any network access."""

    async def get(self, url: str) -> RssHttpResponse: ...


@dataclass(frozen=True)
class RssFeedRegistration:
    """A user-registered feed (RD-15 "사용자 RSS 등록"). `feed_id` is the
    caller's stable identifier for this feed (e.g. a UUID or slug) -- it is
    not derived from `feed_url`, so a user may re-point the same
    registration at a new URL without changing downstream item identity."""

    feed_id: str
    feed_url: str


@dataclass(frozen=True)
class RssFeedEntry:
    guid: str
    title: str
    link: str
    published_at: datetime | None


def _first_text(element: Element, *tags: str) -> str:
    for tag in tags:
        found = element.find(tag)
        if found is not None and found.text:
            return found.text.strip()
    return ""


def _parse_rfc822(raw: str) -> datetime | None:
    if not raw.strip():
        return None
    try:
        parsed = parsedate_to_datetime(raw)
    except (TypeError, ValueError) as exc:
        raise RssParseError(f"RSS <pubDate> is not RFC 822: {raw!r}") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _parse_iso8601(raw: str) -> datetime | None:
    if not raw.strip():
        return None
    try:
        parsed = datetime.fromisoformat(raw.strip().replace("Z", "+00:00"))
    except ValueError as exc:
        raise RssParseError(f"Atom <updated>/<published> is not ISO 8601: {raw!r}") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _parse_rss2_items(channel: Element) -> list[RssFeedEntry]:
    entries: list[RssFeedEntry] = []
    for item in channel.findall("item"):
        title = _first_text(item, "title")
        link = _first_text(item, "link")
        guid = _first_text(item, "guid") or link
        if not guid:
            raise RssParseError("RSS <item> has neither <guid> nor <link>")
        entries.append(
            RssFeedEntry(
                guid=guid,
                title=title,
                link=link,
                published_at=_parse_rfc822(_first_text(item, "pubDate")),
            )
        )
    return entries


def _parse_atom_entries(feed: Element) -> list[RssFeedEntry]:
    entries: list[RssFeedEntry] = []
    for entry in feed.findall(f"{_ATOM_NS}entry"):
        title = _first_text(entry, f"{_ATOM_NS}title")
        link_el = entry.find(f"{_ATOM_NS}link")
        link = link_el.get("href", "") if link_el is not None else ""
        guid = _first_text(entry, f"{_ATOM_NS}id") or link
        if not guid:
            raise RssParseError("Atom <entry> has neither <id> nor <link>")
        raw_date = _first_text(entry, f"{_ATOM_NS}updated", f"{_ATOM_NS}published")
        entries.append(
            RssFeedEntry(
                guid=guid,
                title=title,
                link=link,
                published_at=_parse_iso8601(raw_date),
            )
        )
    return entries


def parse_rss_feed(text: str) -> list[RssFeedEntry]:
    """Parse a feed body as RSS 2.0 or Atom. Raises `RssParseError` for
    anything else (malformed XML, or neither shape) -- an unrecognized feed
    is a parse failure, never a silently empty result."""
    if not text.strip():
        raise RssParseError("feed body is empty")
    try:
        root = fromstring(text)
    except ParseError as exc:
        raise RssParseError(f"feed body is not well-formed XML: {exc}") from exc

    if root.tag == "rss":
        channel = root.find("channel")
        if channel is None:
            raise RssParseError("RSS root has no <channel>")
        return _parse_rss2_items(channel)
    if root.tag == f"{_ATOM_NS}feed":
        return _parse_atom_entries(root)
    raise RssParseError(f"unrecognized feed root element: {root.tag!r}")


def build_research_item_from_entry(
    entry: RssFeedEntry, *, feed_id: str, collected_at: datetime
) -> ResearchItem:
    """Build a `ResearchItem` for one feed entry. `body_ref` is always
    `None` -- there is no parameter through which a caller could set it
    (RD-1 sec.8(b), link_only enforced structurally, exceptionless).

    `known_at=collected_at`, never `entry.published_at` -- the system could
    not have known about the entry before this collection actually polled
    the feed (RD-A1 point-in-time integrity); falls back to `collected_at`
    for `published_at` too when the feed omitted a date entirely.
    """
    item_id = uuid5(_ITEM_ID_NAMESPACE, f"{feed_id}:{entry.guid}")
    return ResearchItem(
        item_id=item_id,
        source_id=RSS_GENERIC_SOURCE_ID,
        kind="news",
        published_at=entry.published_at or collected_at,
        known_at=collected_at,
        instruments=(),
        title=entry.title,
        body_ref=None,
        url=entry.link,
        language="und",
        hash=hashlib.sha256(f"{feed_id}:{entry.guid}".encode()).hexdigest(),
        revision_of=None,
    )


def _retry_after_seconds(headers: Mapping[str, str]) -> float | None:
    for key, value in headers.items():
        if key.lower() == "retry-after":
            try:
                return float(value)
            except ValueError:
                return None
    return None


async def fetch_rss_feed(
    client: RssHttpClient, registration: RssFeedRegistration, *, collected_at: datetime
) -> list[ResearchItem]:
    """Fetch and parse one user-registered feed into `link_only`
    `ResearchItem`s. Raises `RssRateLimited` on HTTP 429 (RD-1 sec.8(c):
    measured per feed, never assumed), `RssApiError` on any other non-2xx
    status."""
    if not registration.feed_url:
        raise RssApiError(f"RSS registration {registration.feed_id!r} has no feed_url")
    response = await client.get(registration.feed_url)
    if response.status_code == 429:
        raise RssRateLimited(
            registration.feed_id,
            retry_after_seconds=_retry_after_seconds(response.headers),
        )
    if not 200 <= response.status_code < 300:
        raise RssApiError(
            f"RSS feed {registration.feed_id!r} returned HTTP {response.status_code}"
        )
    entries = parse_rss_feed(response.text)
    return [
        build_research_item_from_entry(e, feed_id=registration.feed_id, collected_at=collected_at)
        for e in entries
    ]
