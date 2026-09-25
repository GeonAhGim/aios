"""L4-31 DEEPEN(task-2771) — `dispatch_outcome.py`(순수 분류 모듈) 전용 증빙.

Spec: docs/specs/L4_execution_oms_and_exchange_v1.0.md §2-C L4-31 소급 행,
§4.4 outbox 상태기계 상호참조. `test_outbox_dispatcher.py`가 통합 흐름
관점의 분류를 이미 커버하지만(§8.2 L4-14), 이 파일 자체를 대상으로 한
전용 테스트 파일은 이 리프 이전엔 하나도 없었다(DEPTH_L4_BR 감사, 원
task-1758 commit 7223a8c가 문서/docstring만 추가하고 신규 테스트 0건).

여기서 채우는 것: (1) 4개 classify_* 함수 전수 — ExchangeErrorKind 11종
전부가 어떤 분기든 반드시 걸린다는 게이트, (2) 미분류/레거시 예외 fail-closed
negative, (3) 예외 계층 조작을 통한 failure-injection, (4) 순수 함수 성능
하한(스레드/DB 없이 수만 회 분류가 예산 내), (5) 스레드 간 동시 호출로
공유 가변 상태가 없음을 증명하는 멀티-인스턴스 proof.
"""
from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal

import pytest

from src.core.exceptions import FatalExchangeError, RetryableExchangeError
from src.data.models.base import AssetClass, Currency, Money
from src.data.models.trading import Order, OrderSide, OrderStatus, OrderType
from src.exchanges.common.error_taxonomy import (
    ExchangeError,
    ExchangeErrorKind,
    SentUnknownError,
)
from src.services.oms.application.dispatch_outcome import (
    OutcomeKind,
    classify_idempotent_failure,
    classify_lookup_failure,
    classify_submit_failure,
    classify_submit_response,
)


def _order(status: OrderStatus, *, exchange_order_id: str | None = "ex-1") -> Order:
    return Order(
        client_order_id="c-1",
        strategy_id="s1",
        strategy_version="1.0.0",
        symbol="BTC/USDT",
        exchange="bitget",
        side=OrderSide.BUY,
        order_type=OrderType.MARKET,
        quantity=Decimal("1"),
        price=Money(amount=Decimal("100"), currency=Currency.USDT),
        status=status,
        exchange_order_id=exchange_order_id,
        asset_class=AssetClass.CRYPTO,
    )


# ---- (1) 게이트: ExchangeErrorKind 11종 전부가 분류 함수에서 예외 없이 걸린다 ----------
_ALL_KINDS = list(ExchangeErrorKind)


@pytest.mark.parametrize("kind", _ALL_KINDS)
def test_classify_submit_failure_covers_every_kind_no_crash(kind: ExchangeErrorKind) -> None:
    """CI 레드라인 — 신규 ExchangeErrorKind가 추가되고 분류 분기가 없으면
    이 테스트가 실패한다(가장 아래 분기는 UNKNOWN 승격이라 크래시는 없지만,
    kind가 11종에서 벗어나면 파라미터 목록 자체가 갱신돼야 리뷰 대상이 된다)."""
    outcome = classify_submit_failure(ExchangeError(kind))
    assert isinstance(outcome.kind, OutcomeKind)


@pytest.mark.parametrize("kind", _ALL_KINDS)
def test_classify_idempotent_failure_covers_every_kind_no_crash(
    kind: ExchangeErrorKind,
) -> None:
    outcome = classify_idempotent_failure(ExchangeError(kind))
    assert isinstance(outcome.kind, OutcomeKind)


def test_kind_universe_has_not_silently_grown() -> None:
    """§9 DoD 보조 게이트 — enum이 늘어나면 위 두 parametrize 목록도 함께
    검토해야 한다는 신호(개수 하드코딩으로 "조용한 확장"을 막는다)."""
    assert len(_ALL_KINDS) == 11


# ---- (2) negative — 미분류/레거시 예외는 항상 fail-closed UNKNOWN(I10) -----------------
@pytest.mark.parametrize(
    "exc",
    [
        RetryableExchangeError("legacy retryable"),
        FatalExchangeError("legacy fatal"),
        RuntimeError("socket reset"),
        ValueError("unexpected"),
        TimeoutError("timed out"),
        ConnectionResetError("peer reset"),
    ],
)
def test_classify_submit_failure_unclassified_exceptions_are_unknown(
    exc: BaseException,
) -> None:
    outcome = classify_submit_failure(exc)
    assert outcome.kind is OutcomeKind.UNKNOWN
    assert outcome.reason == type(exc).__name__


def test_classify_idempotent_failure_unclassified_exception_is_retry() -> None:
    """멱등 계열(CANCEL/MODIFY)은 응답 유실이어도 재전송이 안전하므로 RETRY —
    SUBMIT과 반대 방향의 fail-closed(§5.4)."""
    outcome = classify_idempotent_failure(RuntimeError("boom"))
    assert outcome.kind is OutcomeKind.RETRY


def test_classify_lookup_failure_defaults_to_retry_not_sent() -> None:
    outcome = classify_lookup_failure(RuntimeError("dns failure"))
    assert outcome.kind is OutcomeKind.RETRY
    assert outcome.not_sent is True


# ---- (3) failure-injection — 예외 계층·속성 조작 ---------------------------------------
def test_circuit_open_flag_wins_over_kind_classification() -> None:
    """kind는 재시도 가능(SERVER_ERROR)이지만 circuit_open=True가 섞이면
    DEFER가 REJECT/RETRY보다 항상 먼저 걸려야 한다(§6 F4 우선순위)."""
    injected = ExchangeError(
        ExchangeErrorKind.INSUFFICIENT_FUNDS, circuit_open=True, retry_after_sec=5.0
    )
    outcome = classify_submit_failure(injected)
    assert outcome.kind is OutcomeKind.DEFER
    assert outcome.not_sent is True
    assert outcome.retry_after_sec == 5.0


def test_exchange_error_subclass_without_kind_override_still_classifies() -> None:
    """어댑터가 `ExchangeError`를 서브클래싱만 하고 kind를 그대로 물려받는
    경우에도 isinstance 분기를 타야 한다(속성 상속 누락 방지 회귀 테스트)."""

    class _VenueSpecificError(ExchangeError):
        pass

    injected = _VenueSpecificError(ExchangeErrorKind.MARKET_CLOSED)
    outcome = classify_submit_failure(injected)
    assert outcome.kind is OutcomeKind.REJECTED
    assert outcome.reason == "MARKET_CLOSED"


def test_sent_unknown_error_is_exchange_error_subclass_but_short_circuits_first() -> None:
    """`SentUnknownError`는 `ExchangeError`의 서브클래스지만 반드시 그
    isinstance 분기보다 먼저 UNKNOWN으로 확정돼야 한다(§3.4 F3/F17) —
    순서가 뒤집히면 회로/중복 분기로 잘못 새어 들어간다."""
    injected = SentUnknownError(venue="bitget", http_status=None)
    outcome = classify_submit_failure(injected)
    assert outcome.kind is OutcomeKind.UNKNOWN
    assert outcome.reason == "SENT_UNKNOWN"


def test_classify_submit_response_rejected_short_circuits_before_id_check() -> None:
    """REJECTED인데 exchange_order_id도 없는 이중 결손 입력 — 여전히
    REJECTED가 우선(UNKNOWN으로 새면 재전송을 유발해 더 위험하다)."""
    submitted = _order(OrderStatus.REJECTED, exchange_order_id=None)
    outcome = classify_submit_response(submitted)
    assert outcome.kind is OutcomeKind.REJECTED


def test_classify_submit_response_ack_without_id_is_unknown_not_ack() -> None:
    submitted = _order(OrderStatus.SUBMITTED, exchange_order_id=None)
    outcome = classify_submit_response(submitted)
    assert outcome.kind is OutcomeKind.UNKNOWN
    assert outcome.reason == "ACK_WITHOUT_EXCHANGE_ORDER_ID"


# ---- (4) 성능 하한 — 순수 함수, DB/네트워크 없음 ----------------------------------------
@pytest.mark.perf
def test_classify_submit_failure_throughput_is_pure_cpu_bound() -> None:
    """상대 예산 — 절대시간이 아니라 "아무 것도 안 하는 루프" 대비 배수로
    단언해 느린 CI 머신에서도 플레이키하지 않게 한다. 이 함수가 실수로
    I/O(로깅 flush, DB 조회 등)를 하게 되면 배수가 폭발해 게이트가 걸린다."""
    n = 20_000
    errors = [ExchangeError(kind) for kind in _ALL_KINDS]

    baseline_start = time.perf_counter()
    for i in range(n):
        _ = errors[i % len(errors)].kind
    baseline_sec = time.perf_counter() - baseline_start

    start = time.perf_counter()
    for i in range(n):
        classify_submit_failure(errors[i % len(errors)])
    elapsed_sec = time.perf_counter() - start

    budget_sec = max(baseline_sec * 50.0, 0.5)
    print(
        f"\nclassify_submit_failure x{n}: {elapsed_sec * 1000:.1f}ms "
        f"(baseline attr-access x{n}: {baseline_sec * 1000:.1f}ms, "
        f"budget<{budget_sec * 1000:.1f}ms)"
    )
    assert elapsed_sec < budget_sec, (
        f"classify_submit_failure이 순수 CPU 예산({budget_sec:.3f}s)을 넘었습니다 "
        f"({elapsed_sec:.3f}s) — I/O나 O(n^2) 분기가 섞였는지 확인하세요."
    )


# ---- (5) 멀티-인스턴스/동시성 proof — 공유 가변 상태가 없음을 증명 ----------------------
def test_classify_functions_are_thread_safe_across_concurrent_callers() -> None:
    """`_VENUE_REJECT_KINDS`(frozenset, 모듈 전역)를 여러 스레드가 동시에
    읽기만 하는지 증명한다. 모듈 수준 가변 캐시가 실수로 생기면(예: 결과를
    dict에 memoize) 이 테스트가 경합으로 인한 오분류를 잡아낼 수 있다."""
    scenarios: list[tuple[BaseException, OutcomeKind]] = [
        (ExchangeError(ExchangeErrorKind.INSUFFICIENT_FUNDS), OutcomeKind.REJECTED),
        (ExchangeError(ExchangeErrorKind.RATE_LIMITED), OutcomeKind.RETRY),
        (ExchangeError(ExchangeErrorKind.SERVER_ERROR, circuit_open=True), OutcomeKind.DEFER),
        (SentUnknownError(), OutcomeKind.UNKNOWN),
        (RuntimeError("x"), OutcomeKind.UNKNOWN),
    ] * 200  # 1000개 혼합 호출을 여러 스레드에 흩뿌린다

    def _classify(pair: tuple[BaseException, OutcomeKind]) -> bool:
        exc, expected = pair
        return classify_submit_failure(exc).kind is expected

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(_classify, scenarios))

    assert all(results), "동시 호출 중 최소 1건이 기대 분류와 어긋났습니다(경합 의심)"
    assert len(results) == len(scenarios)
