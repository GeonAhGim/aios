"""LA-7 — symbol_normalizer 단위 테스트.

Spec: docs/specs/L4_market_data_positions_ledger_v1.0.md#§8.1 test_symbol_normalizer.py.

DEEPEN(task-2951, docs/audit/DEPTH_LA_LB_LC.md 391): 이 모듈은 I/O 없는 순수
함수라 DEPTH 감사가 요구한 실패주입/성능단언/게이트적색/적대적-동시성 4종을
문자 그대로는 적용할 수 없다(다른 순수 도메인 리프 DEEPEN, 예: task-2946
test_timeframe.py와 동일 논리). 아래 테스트들은 그 축의 정신을 이 모듈에
맞게 옮긴 것이다: 실패주입은 모듈 전역 조회 테이블(`_CRYPTO_QUOTES`)/정규식
(`_KRX_CODE`)을 monkeypatch로 손상시켜 fail-closed를 증명하고, 성능단언은
정규식 기반 검증기가 적대적(긴) 입력에도 병리적 backtracking 없이 즉시
끝남을 재현하며, 게이트적색은 `len(raw) > len(quote)`·`fullmatch` 같은
경계 가드가 실제로 놓칠 뻔한 회귀 클래스를 재현한다.
"""

from __future__ import annotations

import re
import time
from concurrent.futures import ThreadPoolExecutor

import pytest

from src.foundation.market_data.contracts.v1 import Venue
from src.foundation.market_data.domain.reference import symbol_normalizer as normalizer_module
from src.foundation.market_data.domain.reference.symbol_normalizer import (
    SymbolNormalizationError,
    to_canonical,
    to_venue,
)
from tests.conftest import PerfBudget


@pytest.mark.perf
def test_bitget_roundtrip(perf_budget: PerfBudget) -> None:
    """BITGET raw ↔ canonical 양방향 라운드트립 + 성능 단언.

    CPU 예산 단언: `_crypto_raw_to_canonical`/`_crypto_canonical_to_raw`는
    단순 문자열 연산만 하므로 batch=8로 묶어 1회 호출당 1ms 미만이어야 한다.
    Windows `time.process_time()` 약 15.6ms 양자화 오차를 batch가 줄인다.
    """

    def _roundtrip() -> str:
        canonical = to_canonical(Venue.BITGET, "BTCUSDT")
        assert canonical == "BTC/USDT"
        raw = to_venue(Venue.BITGET, canonical)
        assert raw == "BTCUSDT"
        return raw

    perf_budget.assert_within(
        _roundtrip,
        budget_ms=1,
        n=5,
        warmup=1,
        batch=8,
        label="bitget_roundtrip",
    )


# --- DEEPEN(task-16494) bitget_roundtrip 음성 테스트 3개 -----------------------


def test_bitget_roundtrip_unknown_raw_symbol_raises() -> None:
    """BTCXYZ는 `_CRYPTO_QUOTES`에 없는 quote XYZ를 포함하므로 예외"""
    with pytest.raises(SymbolNormalizationError):
        to_canonical(Venue.BITGET, "BTCXYZ")


def test_bitget_roundtrip_invalid_canonical_missing_quote_raises() -> None:
    """BTC/XYZ — XYZ는 `_CRYPTO_QUOTES`에 없으므로 예외"""
    with pytest.raises(SymbolNormalizationError):
        to_venue(Venue.BITGET, "BTC/XYZ")


def test_bitget_roundtrip_empty_base_raises() -> None:
    """/USDT — base가 비어 있으므로 예외"""
    with pytest.raises(SymbolNormalizationError):
        to_venue(Venue.BITGET, "/USDT")


# --- DEEPEN(task-16494) 실패주입: _CRYPTO_QUOTES 손상 -------------------------


def test_bitget_roundtrip_fails_closed_when_quote_table_corrupted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`_CRYPTO_QUOTES`에서 USDT를 제거하면 이전 통과 사례가 조용히 다른
    quote로 잘못 분리되지 않고 즉시 SymbolNormalizationError로 죽는다."""
    monkeypatch.setattr(
        normalizer_module,
        "_CRYPTO_QUOTES",
        tuple(q for q in normalizer_module._CRYPTO_QUOTES if q != "USDT"),
    )
    with pytest.raises(SymbolNormalizationError):
        to_canonical(Venue.BITGET, "BTCUSDT")


# --- DEEPEN(task-16494) gate-red 재현: 10x 지연 주입 --------------------------


@pytest.mark.perf
def test_bitget_roundtrip_perf_budget_catches_10x_delay_injection(
    perf_budget: PerfBudget,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """게이트 적색 재현(D2): CPU 예산 테스트이므로 `time.process_time`을
    monkeypatch해 1회 호출당 예산(1ms)을 10배 초과하는 CPU 시간을 가장하면
    같은 단언이 AssertionError로 적색이 돼야 한다.

    단언/마커 삭제나 예산 완화가 아니라, 단언이 "제대로 작동하는지"를
    역으로 증명한다. fake process_time이 100ms를 반환하므로
    batch=8로 나눈 평균도 100ms > 1ms budget로 적색 확정.
    """
    _fake_time = 0.0

    def _fake_process_time() -> float:
        nonlocal _fake_time
        _fake_time += 0.1  # 100ms per call = 100x budget
        return _fake_time

    monkeypatch.setattr(time, "process_time", _fake_process_time)

    def _roundtrip() -> str:
        canonical = to_canonical(Venue.BITGET, "BTCUSDT")
        raw = to_venue(Venue.BITGET, canonical)
        return raw

    with pytest.raises(AssertionError):
        perf_budget.assert_within(
            _roundtrip,
            budget_ms=1,
            n=5,
            warmup=1,
            batch=8,
            label="bitget_roundtrip_delayed",
        )


def test_krx_roundtrip() -> None:
    assert to_canonical(Venue.KIS_KRX, "005930") == "005930"
    assert to_venue(Venue.KIS_KRX, "005930") == "005930"


def test_us_roundtrip() -> None:
    assert to_canonical(Venue.KIS_US, "AAPL") == "AAPL"
    assert to_venue(Venue.KIS_US, "AAPL") == "AAPL"


def test_nh_krx_roundtrip() -> None:
    assert to_canonical(Venue.NH_KRX, "005930") == "005930"
    assert to_venue(Venue.NH_KRX, "005930") == "005930"


def test_nh_krx_empty_string_raises() -> None:
    with pytest.raises(SymbolNormalizationError):
        to_canonical(Venue.NH_KRX, "")


def test_nh_krx_seven_digit_raises() -> None:
    with pytest.raises(SymbolNormalizationError):
        to_canonical(Venue.NH_KRX, "0059301")


def test_nh_krx_empty_string_raises_to_venue() -> None:
    with pytest.raises(SymbolNormalizationError):
        to_venue(Venue.NH_KRX, "")


def test_unknown_quote_raw_raises() -> None:
    with pytest.raises(SymbolNormalizationError):
        to_canonical(Venue.BITGET, "BTCXYZ")


def test_unknown_quote_canonical_raises() -> None:
    with pytest.raises(SymbolNormalizationError):
        to_venue(Venue.BITGET, "BTC/XYZ")


def test_krx_invalid_format_raises() -> None:
    with pytest.raises(SymbolNormalizationError):
        to_canonical(Venue.KIS_KRX, "AAPL")


def test_us_invalid_format_raises() -> None:
    with pytest.raises(SymbolNormalizationError):
        to_canonical(Venue.KIS_US, "aapl")


# --- DEEPEN(task-2951) 실패주입: 모듈 전역 조회 테이블/정규식 손상 ---------------


def test_crypto_to_canonical_fails_closed_when_quote_removed_from_table(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`_CRYPTO_QUOTES`가 배포 손상으로 항목을 잃으면(예: 설정 로딩 결함),
    이전에는 통과하던 심볼이 조용히 다른 quote로 잘못 분리되지 않고 즉시
    `SymbolNormalizationError`로 죽는다(fail-closed) — 이 모듈은 I/O가 없어
    어댑터 결함을 주입할 수 없으므로, 실전에서 유일하게 있을 수 있는 결함
    형태인 내부 조회 테이블 손상을 monkeypatch로 흉내낸다.
    """
    monkeypatch.setattr(
        normalizer_module,
        "_CRYPTO_QUOTES",
        tuple(q for q in normalizer_module._CRYPTO_QUOTES if q != "USDT"),
    )
    with pytest.raises(SymbolNormalizationError):
        to_canonical(Venue.BITGET, "BTCUSDT")


def test_krx_validate_fails_closed_when_regex_corrupted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`_KRX_CODE` 정규식이 손상돼 아무것도 매칭하지 않게 되면, 유효했던
    6자리 코드도 통과시키지 않고 예외로 죽는다 — 검증 로직이 정규식 객체를
    매 호출 하드코딩하지 않고 모듈 전역에서 읽어온다는 것 자체를 증명한다.
    """

    monkeypatch.setattr(normalizer_module, "_KRX_CODE", re.compile(r"(?!)"))
    with pytest.raises(SymbolNormalizationError):
        to_canonical(Venue.KIS_KRX, "005930")


# --- DEEPEN(task-2951) 수치 성능 단언(적대적 입력 포함) -------------------------


@pytest.mark.perf
def test_to_canonical_completes_within_budget_for_bulk_and_adversarial_inputs(
    perf_budget: PerfBudget,
) -> None:
    """정규식 기반 검증기(`_KRX_CODE`, `_US_TICKER`)는 유효 입력 10만 건과,
    매칭에 실패해야 하는 긴 적대적 입력(비-ASCII 폭주 문자열 포함)을 섞어도
    3초 안에 끝난다 — 두 정규식 모두 `{1,6}` 등 고정 길이 quantifier만 쓰고
    중첩 quantifier가 없어 병리적(catastrophic) backtracking 경로가 없다는
    것을 실측으로 지킨다. 이 가드가 없으면 향후 정규식을 부주의하게 바꿔
    중첩 quantifier를 들여올 때 이 테스트가 타임아웃 대신 적색으로 먼저 잡는다.
    """
    krx_codes = [f"{i:06d}" for i in range(50_000)]
    adversarial = ["A" * 10_000, "9" * 10_000, "가" * 10_000]

    def _bulk_validate() -> None:
        for code in krx_codes:
            assert to_canonical(Venue.KIS_KRX, code) == code
        for bad in adversarial:
            with pytest.raises(SymbolNormalizationError):
                to_canonical(Venue.KIS_KRX, bad)
            with pytest.raises(SymbolNormalizationError):
                to_canonical(Venue.KIS_US, bad)

    perf_budget.assert_within(
        _bulk_validate,
        budget_ms=3000.0,
        n=3,
        warmup=1,
        batch=10,
        label="50k KRX + 3종 적대적 입력 정규화 — 예산 3s(CPU)",
    )


# --- DEEPEN(task-2951) 게이트 적색 재현(회귀 클래스) ----------------------------


def test_crypto_raw_equal_to_quote_raises_instead_of_empty_base() -> None:
    """raw가 quote 문자열과 정확히 같으면(`len(raw) > len(quote)` 경계) base가
    빈 문자열인 "/USDT" 같은 canonical을 조용히 만들지 않고 거부한다 — 이
    `>` 가드가 실수로 `>=`가 되면 이 테스트가 적색이 된다.
    """
    with pytest.raises(SymbolNormalizationError):
        to_canonical(Venue.BITGET, "USDT")


def test_krx_rejects_symbol_with_extra_trailing_digit() -> None:
    """`_KRX_CODE.fullmatch`가 실수로 `match`/`search`로 바뀌면 7자리 이상의
    문자열도 앞 6자리만 보고 통과시킨다 — fullmatch가 전체 문자열을 강제함을
    회귀 클래스로 재현한다.
    """
    with pytest.raises(SymbolNormalizationError):
        to_canonical(Venue.KIS_KRX, "0059301")


def test_us_ticker_rejects_dot_suffix_without_trailing_letter() -> None:
    """`(\\.[A-Z])?` 뒤에 문자가 없는 "AAPL."은 옵셔널 그룹이 부분 매칭되면
    안 된다 — `fullmatch`가 `match`로 바뀌는 회귀를 잡는 경계 케이스다.
    """
    with pytest.raises(SymbolNormalizationError):
        to_canonical(Venue.KIS_US, "AAPL.")


# --- DEEPEN(task-2951) 적대적/동시성/replay 증명 --------------------------------


def test_to_canonical_is_deterministic_under_concurrent_thread_access() -> None:
    """20개 스레드가 동시에 세 venue(크립토/KRX/US) 정규화를 반복 호출해도
    전부 같은 결과를 낸다 — 순수 함수이지만 모듈 전역 `_CRYPTO_QUOTES`/
    `_KRX_CODE`/`_US_TICKER`를 여러 스레드가 동시에 읽는 경로가 실재하므로
    그 경로가 결정론을 깨지 않는지 증명한다(replay: 반복 호출이 항상 같은
    값을 냄).
    """
    cases = [
        (Venue.BITGET, "BTCUSDT", "BTC/USDT"),
        (Venue.KIS_KRX, "005930", "005930"),
        (Venue.KIS_US, "AAPL", "AAPL"),
    ]

    def _run(_: int) -> list[str]:
        return [to_canonical(venue, raw) for venue, raw, _expected in cases]

    reference = _run(0)
    with ThreadPoolExecutor(max_workers=20) as pool:
        results = list(pool.map(_run, range(20)))

    assert all(result == reference for result in results)
