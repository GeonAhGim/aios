"""RD-15 (b) -- `adapters/sources/rss_generic.py` unit tests.

Spec: docs/specs/L4_research_data_and_market_ecosystem_v1.0.md sec.9 RD-15
DoD ("link_only 강제, 사용자 RSS 등록").
"""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock

import pytest

from src.foundation.research_data.adapters.sources.rss_generic import (
    RSS_GENERIC_SOURCE_ID,
    RssApiError,
    RssFeedRegistration,
    RssHttpResponse,
    RssParseError,
    RssRateLimited,
    build_research_item_from_entry,
    fetch_rss_feed,
    parse_rss_feed,
)
from tests.support.perf_budget import PerfBudget

_COLLECTED_AT = datetime(2026, 9, 24, 6, 5, 0, tzinfo=timezone.utc)

_RSS2_FEED = """<?xml version="1.0"?>
<rss version="2.0">
  <channel>
    <title>Example Feed</title>
    <item>
      <title>Headline One</title>
      <link>https://example.com/1</link>
      <guid>guid-1</guid>
      <pubDate>Wed, 23 Sep 2026 06:00:00 GMT</pubDate>
    </item>
    <item>
      <title>Headline Two</title>
      <link>https://example.com/2</link>
      <guid>guid-2</guid>
      <pubDate>Wed, 23 Sep 2026 07:00:00 GMT</pubDate>
    </item>
  </channel>
</rss>
"""

_ATOM_FEED = """<?xml version="1.0"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <title>Example Atom Feed</title>
  <entry>
    <title>Atom Entry</title>
    <id>urn:uuid:atom-1</id>
    <link href="https://example.com/atom/1"/>
    <updated>2026-09-23T06:00:00Z</updated>
  </entry>
</feed>
"""


def test_parse_rss_feed_parses_rss2_items() -> None:
    entries = parse_rss_feed(_RSS2_FEED)
    assert len(entries) == 2
    assert entries[0].guid == "guid-1"
    assert entries[0].link == "https://example.com/1"
    assert entries[0].published_at == datetime(2026, 9, 23, 6, 0, 0, tzinfo=timezone.utc)


def test_parse_rss_feed_parses_atom_entries() -> None:
    entries = parse_rss_feed(_ATOM_FEED)
    assert len(entries) == 1
    assert entries[0].guid == "urn:uuid:atom-1"
    assert entries[0].link == "https://example.com/atom/1"
    assert entries[0].published_at == datetime(2026, 9, 23, 6, 0, 0, tzinfo=timezone.utc)


def test_build_research_item_from_entry_forces_link_only() -> None:
    entry = parse_rss_feed(_RSS2_FEED)[0]
    item = build_research_item_from_entry(entry, feed_id="feed-1", collected_at=_COLLECTED_AT)
    assert item.source_id == RSS_GENERIC_SOURCE_ID
    assert item.kind == "news"
    assert item.body_ref is None
    assert item.known_at == _COLLECTED_AT
    assert item.url == "https://example.com/1"


def test_build_research_item_from_entry_is_deterministic_per_feed() -> None:
    entry = parse_rss_feed(_RSS2_FEED)[0]
    first = build_research_item_from_entry(entry, feed_id="feed-1", collected_at=_COLLECTED_AT)
    second = build_research_item_from_entry(entry, feed_id="feed-1", collected_at=_COLLECTED_AT)
    other_feed = build_research_item_from_entry(entry, feed_id="feed-2", collected_at=_COLLECTED_AT)
    assert first.item_id == second.item_id
    assert first.item_id != other_feed.item_id


# --- negative tests (>=3, D2 floor) ----------------------------------------


def test_parse_rss_feed_raises_on_empty_body() -> None:
    with pytest.raises(RssParseError):
        parse_rss_feed("   ")


def test_parse_rss_feed_raises_on_malformed_xml() -> None:
    with pytest.raises(RssParseError):
        parse_rss_feed("<rss><channel><item><title>oops</channel>")


def test_parse_rss_feed_raises_on_unrecognized_root() -> None:
    with pytest.raises(RssParseError):
        parse_rss_feed("<unknown-root/>")


async def test_fetch_rss_feed_rejects_registration_without_url() -> None:
    client = AsyncMock()
    registration = RssFeedRegistration(feed_id="feed-1", feed_url="")
    with pytest.raises(RssApiError):
        await fetch_rss_feed(client, registration, collected_at=_COLLECTED_AT)
    client.get.assert_not_called()


# --- failure injection: transport signals rate limiting / errors -----------


async def test_fetch_rss_feed_raises_rate_limited_on_429_with_retry_after() -> None:
    client = AsyncMock()
    client.get.return_value = RssHttpResponse(
        status_code=429, headers={"Retry-After": "30"}, text=""
    )
    registration = RssFeedRegistration(feed_id="feed-1", feed_url="https://example.com/feed.xml")
    with pytest.raises(RssRateLimited) as exc_info:
        await fetch_rss_feed(client, registration, collected_at=_COLLECTED_AT)
    assert exc_info.value.retry_after_seconds == 30.0


async def test_fetch_rss_feed_raises_api_error_on_non_2xx() -> None:
    client = AsyncMock()
    client.get.return_value = RssHttpResponse(status_code=500, headers={}, text="")
    registration = RssFeedRegistration(feed_id="feed-1", feed_url="https://example.com/feed.xml")
    with pytest.raises(RssApiError):
        await fetch_rss_feed(client, registration, collected_at=_COLLECTED_AT)


async def test_fetch_rss_feed_propagates_transport_failure() -> None:
    client = AsyncMock()
    client.get.side_effect = ConnectionError("feed unreachable")
    registration = RssFeedRegistration(feed_id="feed-1", feed_url="https://example.com/feed.xml")
    with pytest.raises(ConnectionError):
        await fetch_rss_feed(client, registration, collected_at=_COLLECTED_AT)


async def test_fetch_rss_feed_builds_items_on_success() -> None:
    client = AsyncMock()
    client.get.return_value = RssHttpResponse(status_code=200, headers={}, text=_RSS2_FEED)
    registration = RssFeedRegistration(feed_id="feed-1", feed_url="https://example.com/feed.xml")
    items = await fetch_rss_feed(client, registration, collected_at=_COLLECTED_AT)
    assert len(items) == 2
    assert all(item.body_ref is None for item in items)


# --- numeric performance assertion (D2 floor, ADR-2026-09-09-C) ------------


@pytest.mark.perf
def test_parse_rss_feed_throughput_floor(perf_budget: PerfBudget) -> None:
    """2,000 항목 RSS 파싱이 CPU 시간 예산 내여야 한다 — wall clock(
    time.perf_counter)은 CI xdist 부하에 흔들려 `perf_budget`가
    측정하는 `time.process_time()` CPU 시간으로만 단언한다.

    budget_ms=1_000.0 (1초 CPU): 이전 raw perf_counter 단언이 요구한
    throughput > 2,000/s (2,000 항목 ÷ 1초)와 동등한 예산을 ms 단위로
    옮긴 것. batch=8로 묶어 process_time 15.6mstick 양자화 오차를
    호출당 ~2ms로 낮춘다(1초 ÷ 8 ≈ 125ms/번 > tick).
    """
    items_xml = "".join(
        f"<item><title>T{i}</title><link>https://example.com/{i}</link>"
        f"<guid>guid-{i}</guid><pubDate>Wed, 23 Sep 2026 06:00:00 GMT</pubDate></item>"
        for i in range(2_000)
    )
    feed = f'<rss version="2.0"><channel><title>Big</title>{items_xml}</channel></rss>'

    sample = perf_budget.sample(
        lambda: parse_rss_feed(feed),
        batch=8,
    )
    assert len(sample.result) == 2_000  # 결과 정정성

    budget_ms = 1_000.0
    perf_budget.assert_within(
        lambda: parse_rss_feed(feed),
        budget_ms=budget_ms,
        n=3,
        batch=8,
        label="parse_rss_feed 2,000 items",
    )


# --- gate-red reproduction (D2) — 10x delay injection -----------------------


@pytest.mark.perf
def test_parse_rss_feed_perf_budget_catches_10x_delay_injection(
    perf_budget: PerfBudget, monkeypatch: pytest.MonkeyPatch
) -> None:
    """게이트 적색 재현(D2): 성능 예산 단언이 실제로 위반을 포착하는지
    역으로 증명한다. `time.process_time`을 monkeypatch해 1회 호출당
    예산(1,000ms)을 10배 초과하는 CPU 시간을 가장하면
    `assert_within`가 AssertionError로 적색이 돼야 한다.

    단언/마커 삭제나 예산 완화가 아니라, 단언이 "제대로 작동하는지"를
    검증한다.
    """
    items_xml = "".join(
        f"<item><title>T{i}</title><link>https://example.com/{i}</link>"
        f"<guid>guid-{i}</guid><pubDate>Wed, 23 Sep 2026 06:00:00 GMT</pubDate></item>"
        for i in range(2_000)
    )
    feed = f'<rss version="2.0"><channel><title>Big</title>{items_xml}</channel></rss>'

    budget_ms = 1_000.0

    # PerfBudget.sample() 내부에서 time.process_time()을 호출하므로
    # 이를 monkeypatch해 항상 10초(10x budget)를 반환하게 한다.
    _fake_time = 0.0

    def _fake_process_time() -> float:
        nonlocal _fake_time
        _fake_time += 10_000.0  # 10 000ms = 10x budget per call
        return _fake_time

    monkeypatch.setattr("time.process_time", _fake_process_time)

    # 같은 측정/단언을 재사용 — budget을 초과하므로 AssertionError가 있어야 함
    with pytest.raises(AssertionError):
        perf_budget.assert_within(
            lambda: parse_rss_feed(feed),
            budget_ms=budget_ms,
            n=3,
            batch=8,
            label="parse_rss_feed 2,000 items delay-injected",
        )
