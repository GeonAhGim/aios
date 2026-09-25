"""LA-3 — session_rules/known_venues 테스트.

Spec: docs/specs/L4_market_data_positions_ledger_v1.0.md#§8.1, §9.2 LA-3.
DoD 행 전부: KRX 15:30 KST 마감 == 06:30 UTC, DST 전후 US 마감(EST/EDT 각각),
조기폐장일, 휴장일 is_open=False, 크립토 next_open == at.
"""

from __future__ import annotations

import threading
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from time import perf_counter
from zoneinfo import ZoneInfo

import pytest

from scripts.check_code_language import HANGUL, count_file
from scripts.check_code_language import main as check_code_language_main
from src.foundation.market_data.contracts.v1 import Venue
from src.foundation.market_data.domain.calendar.known_venues import KNOWN_SESSIONS
from src.foundation.market_data.domain.calendar.session_rules import (
    CalendarExhaustedError,
    VenueCalendar,
)


def _calendar(
    venue: Venue,
    *,
    holidays: frozenset[date] = frozenset(),
    early_closes: dict[date, time] | None = None,
) -> VenueCalendar:
    spec = KNOWN_SESSIONS[venue.value]
    return VenueCalendar(
        venue=venue.value,
        tz=spec.tz,
        regular=spec,
        holidays=holidays,
        early_closes=early_closes or {},
    )


def test_krx_close_15_30_kst_equals_06_30_utc() -> None:
    cal = _calendar(Venue.KIS_KRX)
    windows = cal.sessions_for(date(2026, 9, 4))  # 금요일, 정규 거래일
    assert len(windows) == 1
    assert windows[0].close_at.astimezone(timezone.utc) == datetime(
        2026, 9, 4, 6, 30, tzinfo=timezone.utc
    )


def test_us_close_est_before_dst() -> None:
    cal = _calendar(Venue.KIS_US)
    windows = cal.sessions_for(date(2026, 1, 6))  # 화요일, 겨울(EST, UTC-5)
    assert windows[0].close_at.astimezone(timezone.utc) == datetime(
        2026, 1, 6, 21, 0, tzinfo=timezone.utc
    )


def test_us_close_edt_after_dst() -> None:
    cal = _calendar(Venue.KIS_US)
    windows = cal.sessions_for(date(2026, 7, 7))  # 화요일, 여름(EDT, UTC-4)
    assert windows[0].close_at.astimezone(timezone.utc) == datetime(
        2026, 7, 7, 20, 0, tzinfo=timezone.utc
    )


def test_early_close_day_shortens_session() -> None:
    day = date(2026, 9, 4)
    cal = _calendar(Venue.KIS_KRX, early_closes={day: time(13, 0)})
    windows = cal.sessions_for(day)
    assert len(windows) == 1
    assert windows[0].kind == "EARLY_CLOSE"
    assert windows[0].close_at == datetime(2026, 9, 4, 13, 0, tzinfo=ZoneInfo("Asia/Seoul"))


def test_holiday_is_open_false() -> None:
    day = date(2026, 9, 4)
    cal = _calendar(Venue.KIS_KRX, holidays=frozenset({day}))
    at = datetime(2026, 9, 4, 10, 0, tzinfo=ZoneInfo("Asia/Seoul"))
    assert cal.is_open(at) is False
    assert cal.sessions_for(day) == []


def test_regular_trading_day_is_open_during_session() -> None:
    cal = _calendar(Venue.KIS_KRX)
    at = datetime(2026, 9, 4, 10, 0, tzinfo=ZoneInfo("Asia/Seoul"))
    assert cal.is_open(at) is True


def test_crypto_next_open_equals_at() -> None:
    cal = _calendar(Venue.BITGET)
    at = datetime(2026, 9, 4, 3, 17, tzinfo=timezone.utc)
    assert cal.next_open(at) == at
    assert cal.is_open(at) is True


def test_crypto_sessions_for_spans_full_day() -> None:
    cal = _calendar(Venue.BITGET)
    day = date(2026, 9, 4)
    windows = cal.sessions_for(day)
    assert len(windows) == 1
    assert windows[0].kind == "CONTINUOUS"
    assert windows[0].open_at == datetime(2026, 9, 4, tzinfo=timezone.utc)
    assert windows[0].close_at == datetime(2026, 9, 5, tzinfo=timezone.utc)


def test_next_open_returns_at_when_already_in_session() -> None:
    cal = _calendar(Venue.KIS_KRX)
    at = datetime(2026, 9, 4, 10, 0, tzinfo=ZoneInfo("Asia/Seoul"))
    assert cal.next_open(at) == at


def test_next_open_skips_weekend_to_monday() -> None:
    cal = _calendar(Venue.KIS_KRX)
    saturday = datetime(2026, 9, 5, 10, 0, tzinfo=ZoneInfo("Asia/Seoul"))
    next_open = cal.next_open(saturday)
    assert next_open == datetime(2026, 9, 7, 9, 0, tzinfo=ZoneInfo("Asia/Seoul"))


def test_next_open_raises_when_calendar_exhausted() -> None:
    day = date(2026, 9, 4)
    all_holidays = frozenset(day + timedelta(days=i) for i in range(60))
    cal = _calendar(Venue.KIS_KRX, holidays=all_holidays)
    at = datetime(2026, 9, 4, 10, 0, tzinfo=ZoneInfo("Asia/Seoul"))
    with pytest.raises(CalendarExhaustedError):
        cal.next_open(at)


# ---- DEEPEN(task-2947, DEPTH audit task-2723/docs/audit/DEPTH_LA_LB_LC.md) ----
# negative(3rd) / failure-injection / numeric performance / gate-red /
# adversarial+replay+concurrency evidence for LA-3.


def test_is_open_false_before_open_and_after_close_on_trading_day() -> None:
    """Negative (3rd) -- on an ordinary trading day `is_open` must be False
    both strictly before the session opens and at/after it closes, not only
    on holidays or during the exhausted-calendar edge case already covered
    above by the other two negative tests."""
    cal = _calendar(Venue.KIS_KRX)
    day = date(2026, 9, 4)  # Friday, regular trading day
    before_open = datetime(2026, 9, 4, 8, 59, tzinfo=ZoneInfo("Asia/Seoul"))
    at_close = datetime(2026, 9, 4, 15, 30, tzinfo=ZoneInfo("Asia/Seoul"))
    after_close = datetime(2026, 9, 4, 16, 0, tzinfo=ZoneInfo("Asia/Seoul"))
    assert cal.is_open(before_open) is False
    assert cal.is_open(at_close) is False  # close_at is an exclusive upper bound
    assert cal.is_open(after_close) is False
    assert cal.sessions_for(day) != []  # sanity: the day itself is a trading day


def test_is_open_and_next_open_propagate_failure_from_corrupted_sessions_for(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Failure-injection -- if `sessions_for` ever fails unexpectedly (e.g. a
    corrupted holiday/early-close data load upstream), `is_open`/`next_open`
    must not swallow it into a false "closed" or "open" result: silently
    defaulting either way risks blocking real trading or letting an order
    through outside session hours. Neither method wraps the call in
    try/except, so this pins that fail-closed (fail-loud) shape against
    regression."""

    def _boom(self: VenueCalendar, day: date) -> list[object]:
        raise RuntimeError("simulated corrupted session data")

    monkeypatch.setattr(VenueCalendar, "sessions_for", _boom)
    cal = _calendar(Venue.KIS_KRX)
    at = datetime(2026, 9, 4, 10, 0, tzinfo=ZoneInfo("Asia/Seoul"))

    with pytest.raises(RuntimeError, match="simulated corrupted session data"):
        cal.is_open(at)
    with pytest.raises(RuntimeError, match="simulated corrupted session data"):
        cal.next_open(at)


@pytest.mark.perf
def test_next_open_throughput_within_latency_budget() -> None:
    """Numeric performance assertion -- 5,000 `next_open` calls spanning
    ~13.7 years of daily timestamps (pure in-memory computation, no I/O) must
    finish well inside a generous budget. A regression that turns the
    `_MAX_LOOKAHEAD_DAYS` scan or `sessions_for` into something quadratic in
    the lookahead window would blow this budget long before it hurt in
    production."""
    cal = _calendar(Venue.KIS_KRX)
    base = datetime(2026, 1, 1, 10, 0, tzinfo=ZoneInfo("Asia/Seoul"))
    at_times = [base + timedelta(days=i) for i in range(5_000)]

    started = perf_counter()
    for at in at_times:
        cal.next_open(at)
    elapsed = perf_counter() - started

    assert elapsed < 2.0, f"5,000 next_open() calls took {elapsed:.2f}s, over the 2.0s budget"


_KNOWN_VENUES_PATH = (
    Path(__file__).resolve().parents[3]
    / "src/foundation/market_data/domain/calendar/known_venues.py"
)

# Verbatim pre-translation module docstring from commit c33c2030 (task-2114),
# before ADR-2026-09-07-A's code_language ratchet forced it to English.
_PRE_TRANSLATION_MODULE_SOURCE = '''"""LA-3 — 알려진 venue의 세션 스펙 상수(KRX·US·크립토).

Spec: docs/specs/L4_market_data_positions_ledger_v1.0.md#§2.2, §9.2 LA-3.
개장·마감 시각은 각 거래소 정규시장 공지 기준 표준 사실이다(휴장일 목록은
여기 포함하지 않는다 — 그 목록은 R4 미확인 대상이며 LA-12 yaml 로더가 별도
공급한다). NYSE·NASDAQ은 정규장 시간이 같아 `KIS_US` 하나로 취급한다.

KIS_KRX의 `close_time=15:30`은 연속경쟁매매(09:00~15:20)와 그 뒤에 이어지는
종가단일가매매(15:20~15:30, 당일 종가를 단일가로 결정하는 호가 집중 구간)를
모두 포함한 정규장 마감 시각이다 — 유가증권시장 업무규정(한국거래소)상
정규시장은 이 종가단일가매매 구간까지가 하나의 정규 세션이므로, 개장·폐장
여부 판정(`VenueCalendar.is_open`)에는 별도 세션 구간으로 쪼개지 않는다.
(참고: https://easylaw.go.kr/CSP/CnpClsMain.laf?csmSeq=1701 — "매매거래일·
거래시간 및 거래 원칙 등")
"""
'''


def test_gate_code_language_ratchet_flags_the_pre_translation_korean_docstring(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Gate-red reproduction -- commit c33c2030 (task-2114) had to translate
    part of this exact known_venues.py module docstring (the KIS_KRX
    close_time paragraph) from Korean to English because, together with
    growth elsewhere, it pushed scripts/check_code_language.py's Hangul
    comment/docstring ratchet over its repo-wide budget. The file's first
    paragraph predates the ADR-2026-09-07-A ratchet and is grandfathered
    (existing Hangul is not retro-converted, only growth is blocked).

    This drives the gate's actual entrypoint (`main`), not just the
    `count_file` helper, against a target tree containing the reconstructed
    pre-translation module and a baseline pinned to the shipped file's
    grandfathered count. A gate that was patched to always "pass" (e.g. its
    `total > baseline` branch short-circuited to `return 0`) would still make
    `count_file` report a higher number here, but would no longer reproduce
    the red exit code -- this asserts on `main`'s actual return value and
    printed FAIL line so that regression is caught, not just the raw count."""
    current_count = count_file(_KNOWN_VENUES_PATH)
    assert current_count == 4  # grandfathered first-paragraph debt only

    poisoned = tmp_path / "known_venues_pre_translation.py"
    poisoned.write_text(_PRE_TRANSLATION_MODULE_SOURCE, encoding="utf-8")
    assert count_file(poisoned) > current_count
    assert HANGUL.search(_PRE_TRANSLATION_MODULE_SOURCE) is not None

    baseline = tmp_path / "baseline.txt"
    baseline.write_text(f"{current_count}\n", encoding="utf-8")

    # count_tree() resolves scanned files relative to the module-level ROOT
    # constant, so the target directory must live under it for a real run.
    monkeypatch.setattr("scripts.check_code_language.ROOT", tmp_path)

    exit_code = check_code_language_main(
        ["--target", str(tmp_path), "--baseline", str(baseline)]
    )
    captured = capsys.readouterr()

    assert exit_code == 1  # gate-red: FAIL, growth over the baseline
    assert "FAIL" in captured.out
    assert "limit exceeded" in captured.out
    # baseline file must be left untouched on a FAIL -- the gate never writes on red
    assert baseline.read_text(encoding="utf-8").strip() == str(current_count)


def test_is_open_raises_typeerror_for_naive_datetime_adversarial_input() -> None:
    """Adversarial input -- a naive datetime (no tzinfo) must not be silently
    compared against this calendar's tz-aware session windows and produce a
    wrong answer. Python's own aware/naive comparison rules raise TypeError
    here, and this pins that as the actual (fail-loud, not fail-silent)
    behavior a caller who skips the repo's tz-aware-UTC convention hits.

    `trading_day_of` calls `at.astimezone(self.tz)`, which per Python
    semantics does NOT raise for a naive `at` -- it presumes `at` is already
    in the *host's* local timezone and converts from there. The TypeError
    only fires later, when the resulting (aware) session window is compared
    against the still-naive `at`, and only if that day actually has a
    session. On a host whose local UTC offset differs enough from KRX's
    fixed +9, a Thursday 10:00 naive value can roll onto a Saturday in KRX
    time, `sessions_for` then returns `[]`, no comparison ever happens, and
    `is_open` returns False instead of raising (observed on a UTC-08 host).

    `astimezone()`'s presumed-local-time shift is bounded by the full range
    of real UTC offsets (-12..+14), so relative to KRX's fixed +9 the result
    lands at most 5h earlier or 21h later than the naive wall-clock value --
    i.e. on the same day or the following day, never further. Anchoring on a
    Wednesday 10:00 keeps both possible outcomes (Wed or Thu) a KRX trading
    weekday with no configured holiday, so the aware/naive comparison is
    always reached regardless of host timezone."""
    cal = _calendar(Venue.KIS_KRX)
    naive_at = datetime(2026, 9, 2, 10, 0)  # Wed -- no tzinfo, violates repo convention
    with pytest.raises(TypeError):
        cal.is_open(naive_at)
    with pytest.raises(TypeError):
        cal.next_open(naive_at)


def test_next_open_is_deterministic_across_repeated_replay() -> None:
    """Replay proof -- callers that reprocess the same event (e.g. an
    at-least-once message queue redelivering a market-data tick) must get
    identical results from repeated `next_open(at)` calls on the same input.
    Hidden mutable state (e.g. a memoizing cache keyed wrong) would show up
    as drift across replays."""
    cal = _calendar(Venue.KIS_KRX)
    at = datetime(2026, 9, 5, 10, 0, tzinfo=ZoneInfo("Asia/Seoul"))  # Saturday
    results = [cal.next_open(at) for _ in range(25)]
    assert len(set(results)) == 1


def test_concurrent_next_open_calls_are_consistent_and_thread_safe() -> None:
    """Concurrency proof -- VenueCalendar is a frozen dataclass shared across
    threads/async tasks that price multiple venues concurrently in
    production; it holds no mutable state, so concurrent calls must return
    identical, correct results with no lock. Runs `next_open` from many
    threads on the same instance and asserts they all agree with the
    single-threaded answer, catching any future edit that adds shared
    mutable state (e.g. an unsynchronized cache)."""
    cal = _calendar(Venue.KIS_KRX)
    saturday = datetime(2026, 9, 5, 10, 0, tzinfo=ZoneInfo("Asia/Seoul"))
    expected = datetime(2026, 9, 7, 9, 0, tzinfo=ZoneInfo("Asia/Seoul"))

    results: list[datetime | None] = [None] * 50

    def _call(i: int) -> None:
        results[i] = cal.next_open(saturday)

    threads = [threading.Thread(target=_call, args=(i,)) for i in range(50)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert all(r == expected for r in results)
