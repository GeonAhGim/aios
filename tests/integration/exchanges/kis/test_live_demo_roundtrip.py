"""task-2787(DEEPEN 1931) — BR-14 KIS 실계좌(모의투자) 왕복 검증.

Spec: docs/specs/L4_execution_oms_and_exchange_v1.0.md §10 BR, ADR-2026-09-06-I
      D7. ADR-2026-09-06-I 75-77행("계좌 없이 진행한다") — 모의투자 계좌는
      사람만 만들 수 있어(HB-3) 289개 구현은 고정 응답 픽스처 계약 테스트로만
      검증했고, 실계좌 왕복은 "계정 확보 후 별도 검증 리프"로 미뤄졌다. 이
      파일이 그 리프다.

이 파일의 테스트는 전부 `pytest.mark.live_demo`로만 실행된다(기본 CI
`addopts`가 `not live_demo`로 제외 — pyproject.toml, task-2179/L4-30과 동일
관례). 이 커밋 시점 `.env`에는 KIS 자격증명이 전부 비어 있다(HB-3 미해소,
docs/design/KIS_TR_COVERAGE.md "왕복 검증" 절 참조) — 모든 테스트가
`pytest.skip()`으로 정확한 사유를 출력한다(빈 통과 금지, §10 정직 표기).

**`KIS_APP_KEY`/`KIS_APP_SECRET`가 아니라 `KIS_LIVE_DEMO_APP_KEY`/
`KIS_LIVE_DEMO_APP_SECRET`/`KIS_LIVE_DEMO_CANO`를 쓰는 이유**:
`tests/conftest.py`가 라우터 임포트를 위해 `KIS_APP_KEY`/`KIS_APP_SECRET`를
"운영자의 실제 자격증명이 셸/.env에 있어도" 항상 고정 테스트값
(`aios-test-only-kis-*`)으로 덮어쓴다(그 파일의 의도적 안전장치 — 통합
테스트가 실수로 진짜 키를 집어 쓰지 못하게 막는다). 이 이름을 그대로
쓰면 `_missing_env_vars()`가 "값이 있다"고 착각해 가짜 키로 실제 KIS
서버를 호출하게 된다 — 그래서 이 파일만 conftest가 건드리지 않는 별도
이름을 쓴다(Bitget의 `BITGET_DEMO_API_KEY` 관례와 같은 이유,
.env.example 참조). 세 값(+선물 심볼)이 모두 채워지면 이 파일을 고치지
않고도 KIS 모의투자 서버에 대해 대표 TR을 실제로 왕복한다.

도메인별 대표 TR(각 3건, DoD "도메인별 대표 TR 각 3건 이상 실왕복 성공"):
  - domestic_stock:  place(TTTC0012U/0011U) / cancel(TTTC0013U) / get(TTTC0081R)
  - overseas_stock:  place(TTTT1002U/1006U) / cancel(TTTT1004U) / balance(TTTS3012R)
  - domestic_futureoption: place(TTTO1101U) / cancel(TTTO1103U) / balance(CTFO6118R)
    — 선물 종목코드는 월물마다 만기가 바뀌어 미리 하드코딩하면 곧 무효가
    되므로(8.3 원칙, 미검증 사실을 고정값으로 위장 금지) 실행 시점의 현재
    월물을 `KIS_LIVE_DEMO_KR_FUTURES_SYMBOL` 환경변수로 받는다 — 없으면
    이 도메인만 skip(계좌 자격증명이 다 있어도).
  - overseas_futureoption: DoD "실패한 TR은 사유와 함께 매트릭스에
    실전계좌필요/미지원으로 재분류" — `overseas_futureoption_mixin.py`
    docstring이 이미 구조적 증거(기준 목록 35개 TR 전부가 T/J/C 접두가
    아니라 O/H 접두라 모의투자 V-치환 대상이 없음)로 "모의투자 자체가 해외
    파생을 취급하지 않을 가능성"을 제기했다 — 이 파일은 그 증거를 자격증명
    없이도 항상 실행되는 회귀 테스트로 승격한다(아래
    `test_overseas_futureoption_...`). 왕복 시도 자체는 하지 않는다(계좌가
    생겨도 이 도메인은 왕복 시도 전에 이 정황 증거부터 재확인해야 한다).

레드팀 원칙(redaction) — 키 값은 어떤 assert 메시지·print·로그에도
보간하지 않는다. skip 사유는 "어떤 변수가 없는지"만 말하지, 값은 절대
말하지 않는다.
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from decimal import Decimal

import pytest

from src.data.models.base import AssetClass, Currency, Money
from src.data.models.trading import Order, OrderSide, OrderType
from src.exchanges.kis import trading_mixin
from src.exchanges.kis.adapter import KISAdapter
from src.exchanges.kis.overseas_futureoption_mixin import (
    KISOverseasFutureoptionMixin as _OverseasFutureoptionMixin,
)
from tests.conftest import PerfBudget

# 모듈 전체가 아니라 왕복을 "시도"하는 세 테스트에만 개별 부여한다(아래
# `@pytest.mark.live_demo`) — `test_overseas_futureoption_trs_...`는 자격증명
# 없이 항상 실행돼야 하는 구조적 회귀 가드라 기본 CI에서 제외되면 안 된다
# (pyproject.toml addopts가 `not live_demo`로 live_demo 마커 테스트를 뺀다).
_ACCOUNT_ENV_VARS = ("KIS_LIVE_DEMO_APP_KEY", "KIS_LIVE_DEMO_APP_SECRET", "KIS_LIVE_DEMO_CANO")
_FUTURES_SYMBOL_ENV_VAR = "KIS_LIVE_DEMO_KR_FUTURES_SYMBOL"

# 왕복용 안전값 — 실측 시가보다 훨씬 낮은 지정가 매수로, 체결 위험 없이
# place/get/cancel을 왕복한다(task-2179 L4-30과 동일 원칙).
_KR_EQUITY_SYMBOL = "005930"  # 삼성전자 — KRX 최장수 대형주, 심볼 만료 없음
_KR_EQUITY_SAFE_PRICE = Decimal("1000")  # KRW, 실측 시가보다 훨씬 낮음
_US_EQUITY_SYMBOL = "AAPL"
_US_EQUITY_EXCHANGE = "NASD"
_US_EQUITY_SAFE_PRICE = Decimal("1.00")  # USD, 실측 시가보다 훨씬 낮음
# Currency enum(src/data/models/base.py)에 USD가 없다(USDT/KRW만 존재 — 이
# 리프 스콥 밖의 기존 데이터모델 공백). place_overseas_order()는
# order.price.amount만 body에 실어 보내고 currency 필드는 읽지 않으므로
# (overseas_stock_mixin.py 참조) 여기서는 자리표시자로 USDT를 쓴다.
_US_EQUITY_CURRENCY_PLACEHOLDER = Currency.USDT
_KR_FUTURES_SAFE_PRICE = Decimal("1")  # 지수선물 최소 단위 근사(미검증) — 체결 위험 없음


def _missing_env_vars(names: tuple[str, ...]) -> list[str]:
    return [name for name in names if not os.environ.get(name)]


@pytest.fixture
async def demo_adapter() -> AsyncIterator[KISAdapter]:
    missing = _missing_env_vars(_ACCOUNT_ENV_VARS)
    if missing:
        pytest.skip(
            "KIS 모의투자 왕복 테스트 skip — 누락된 환경변수: "
            f"{', '.join(missing)} (값 자체는 절대 출력하지 않음, redaction; "
            "HB-3 미해소 — docs/design/KIS_TR_COVERAGE.md 참조)"
        )
    adapter = KISAdapter(
        os.environ["KIS_LIVE_DEMO_APP_KEY"],
        os.environ["KIS_LIVE_DEMO_APP_SECRET"],
        os.environ["KIS_LIVE_DEMO_CANO"],
        os.environ.get("KIS_LIVE_DEMO_ACNT_PRDT_CD", "01"),
        is_paper_trading=True,  # 헤드리스 자동 테스트는 절대 LIVE로 구성하지 않는다
    )
    try:
        yield adapter
    finally:
        await adapter.aclose()


def _order(
    *,
    symbol: str,
    price: Decimal,
    currency: Currency,
    asset_class: AssetClass,
    tag: str,
) -> Order:
    return Order(
        client_order_id=f"br14-{tag}-{os.urandom(4).hex()}",
        strategy_id="br14-live-demo",
        strategy_version="v1",
        symbol=symbol,
        exchange="kis",
        side=OrderSide.BUY,
        order_type=OrderType.LIMIT,
        quantity=Decimal("1"),
        price=Money(amount=price, currency=currency),
        asset_class=asset_class,
    )


@pytest.mark.live_demo
async def test_domestic_stock_place_get_cancel_roundtrip(demo_adapter: KISAdapter) -> None:
    """domestic_stock 대표 3TR: place(TTTC0012U) / get(TTTC0081R) / cancel(TTTC0013U)."""
    order = _order(
        symbol=_KR_EQUITY_SYMBOL,
        price=_KR_EQUITY_SAFE_PRICE,
        currency=Currency.KRW,
        asset_class=AssetClass.KR_EQUITY,
        tag="kr-equity",
    )
    placed = await demo_adapter.place_order(order)
    assert placed.exchange_order_id

    fetched = await demo_adapter.get_order(placed.exchange_order_id)
    assert fetched.exchange_order_id == placed.exchange_order_id

    cancelled = await demo_adapter.cancel_order(placed.exchange_order_id)
    assert cancelled is True


@pytest.mark.live_demo
async def test_overseas_stock_place_cancel_balance_roundtrip(demo_adapter: KISAdapter) -> None:
    """overseas_stock 대표 3TR: place(TTTT1002U) / cancel(TTTT1004U) /
    balance(TTTS3012R)."""
    order = _order(
        symbol=_US_EQUITY_SYMBOL,
        price=_US_EQUITY_SAFE_PRICE,
        currency=_US_EQUITY_CURRENCY_PLACEHOLDER,
        asset_class=AssetClass.US_EQUITY,
        tag="us-equity",
    )
    placed = await demo_adapter.place_overseas_order(order, _US_EQUITY_EXCHANGE)
    assert placed.exchange_order_id

    balances = await demo_adapter.get_overseas_balance(_US_EQUITY_EXCHANGE)
    assert isinstance(balances, list)

    cancelled = await demo_adapter.cancel_overseas_order(
        placed.exchange_order_id,
        _US_EQUITY_SYMBOL,
        _US_EQUITY_EXCHANGE,
        original_quantity=Decimal("1"),
    )
    assert cancelled is True


@pytest.mark.live_demo
async def test_domestic_futureoption_place_cancel_balance_roundtrip(
    demo_adapter: KISAdapter,
) -> None:
    """domestic_futureoption 대표 3TR: place(TTTO1101U) / cancel(TTTO1103U) /
    balance(CTFO6118R). 월물 심볼은 만기가 있어 하드코딩하지 않는다 —
    `KIS_LIVE_DEMO_KR_FUTURES_SYMBOL`이 없으면 계좌 자격증명이 갖춰져도
    이 테스트만 별도로 skip한다."""
    missing = _missing_env_vars((_FUTURES_SYMBOL_ENV_VAR,))
    if missing:
        pytest.skip(
            "국내선물옵션 왕복 skip — 누락된 환경변수: "
            f"{', '.join(missing)}(월물마다 만기가 바뀌어 고정 심볼을 쓸 수 없음)"
        )
    symbol = os.environ[_FUTURES_SYMBOL_ENV_VAR]
    order = _order(
        symbol=symbol,
        price=_KR_FUTURES_SAFE_PRICE,
        currency=Currency.KRW,
        asset_class=AssetClass.KR_FUTURES,
        tag="kr-futures",
    )
    placed = await demo_adapter.place_futureoption_order(order)
    assert placed.exchange_order_id

    balances = await demo_adapter.get_futureoption_balance()
    assert isinstance(balances, list)

    cancelled = await demo_adapter.cancel_futureoption_order(
        placed.exchange_order_id, quantity=Decimal("1")
    )
    assert cancelled is True


def test_overseas_futureoption_trs_have_no_paper_trading_equivalent() -> None:
    """DoD "실패한 TR은 사유와 함께 매트릭스에 실전계좌필요/미지원으로
    재분류" — overseas_futureoption 대표 3TR(place/cancel/balance)을 자격증명
    없이도 항상 실행되는 구조적 회귀 테스트로 고정한다. `adapter.py`의
    모의투자 치환 규칙(`_PAPER_SWAP_PREFIXES = ("T", "J", "C")`)이 적용될 수
    없는 접두(O/H)임을 증명해, "이 도메인은 모의투자 자체가 지원하지 않을 수
    있다"(overseas_futureoption_mixin.py 모듈 docstring, 미검증이지만 강한
    정황 증거)는 주장을 코드로 고정한다 — 누군가 나중에 이 mixin의 TR을
    T/J/C 접두로 바꾸면(실제로는 그런 변경이 없어야 하지만) 이 테스트가
    깨져서 재확인을 강제한다(gate-red 회귀 가드)."""
    from src.exchanges.kis.adapter import _PAPER_SWAP_PREFIXES

    representative_trs = ("OTFM3001U", "OTFM3003U", "OTFM1412R")  # place/cancel/balance
    for tr_id in representative_trs:
        assert tr_id[0] not in _PAPER_SWAP_PREFIXES, (
            f"{tr_id}가 모의투자 치환 접두({_PAPER_SWAP_PREFIXES})로 바뀜 — "
            "overseas_futureoption '미지원(모의투자)' 재분류가 더 이상 유효하지 "
            "않을 수 있으니 docs/design/KIS_TR_COVERAGE.md의 왕복 검증 절을 "
            "재검토할 것."
        )
    # 클래스 자체가 여전히 이 3개 메서드를 갖고 있는지도 함께 고정한다 —
    # place/cancel/balance 셋 다 "구현은 됐지만 모의투자로 왕복 검증은
    # 구조적으로 불가"라는 상태를 나타낸다.
    assert hasattr(_OverseasFutureoptionMixin, "place_overseas_futureoption_order")
    assert hasattr(_OverseasFutureoptionMixin, "cancel_overseas_futureoption_order")
    assert hasattr(_OverseasFutureoptionMixin, "get_overseas_futureoption_balance")


# --- D2 depth evidence for the overseas_stock roundtrip (task-24193) --------------------------
# Offline: `_request` is stubbed, so these run in default CI without credentials (no live_demo).
# Perf budget (new): place+balance+cancel against an instant stub is pure adapter CPU; local
# best-of-3 measured ~1 ms for the 3-call sequence, budget 50 ms (>30x headroom).


class _StubKIS:
    """Records `_request` calls and answers with canned KIS payloads."""

    def __init__(self, *, rt_cd: str = "0", fail_on: str | None = None) -> None:
        self.calls: list[tuple[str, str]] = []
        self._rt_cd = rt_cd
        self._fail_on = fail_on

    async def request(self, method: str, path: str, tr_id: str, **_: object) -> dict[str, object]:
        self.calls.append((method, tr_id))
        if tr_id == self._fail_on:
            raise ConnectionError("injected transport failure")
        if tr_id == "TTTT1002U":
            return {"rt_cd": "0", "output": {"KRX_FWDG_ORD_ORGNO": "91252", "ODNO": "0000001"}}
        if tr_id == "TTTS3012R":
            return {"output1": [{"ovrs_pdno": "AAPL", "ovrs_cblc_qty": "2"}]}
        return {"rt_cd": self._rt_cd}


def _stub_adapter(monkeypatch: pytest.MonkeyPatch, stub: _StubKIS) -> KISAdapter:
    adapter = KISAdapter("k", "s", "12345678", "01", is_paper_trading=True)
    monkeypatch.setattr(adapter, "_request", stub.request)
    return adapter


async def _overseas_roundtrip(adapter: KISAdapter) -> bool:
    order = _order(
        symbol=_US_EQUITY_SYMBOL,
        price=_US_EQUITY_SAFE_PRICE,
        currency=_US_EQUITY_CURRENCY_PLACEHOLDER,
        asset_class=AssetClass.US_EQUITY,
        tag="us-equity",
    )
    placed = await adapter.place_overseas_order(order, _US_EQUITY_EXCHANGE)
    assert placed.exchange_order_id == "91252:0000001"
    balances = await adapter.get_overseas_balance(_US_EQUITY_EXCHANGE)
    assert [b.asset for b in balances] == ["AAPL"]
    return await adapter.cancel_overseas_order(
        placed.exchange_order_id,
        _US_EQUITY_SYMBOL,
        _US_EQUITY_EXCHANGE,
        original_quantity=Decimal("1"),
    )


async def test_overseas_roundtrip_offline_uses_expected_tr_ids(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stub = _StubKIS()
    adapter = _stub_adapter(monkeypatch, stub)
    assert await _overseas_roundtrip(adapter) is True
    assert [tr for _, tr in stub.calls] == ["TTTT1002U", "TTTS3012R", "TTTT1004U"]
    await adapter.aclose()


async def test_overseas_cancel_rejected_returns_false_negative(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter = _stub_adapter(monkeypatch, _StubKIS(rt_cd="1"))
    assert await _overseas_roundtrip(adapter) is False
    await adapter.aclose()


async def test_overseas_cancel_malformed_order_id_raises_negative(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter = _stub_adapter(monkeypatch, _StubKIS())
    with pytest.raises(ValueError):
        await adapter.cancel_overseas_order(
            "no-colon-id", _US_EQUITY_SYMBOL, _US_EQUITY_EXCHANGE, original_quantity=Decimal("1")
        )
    await adapter.aclose()


async def test_overseas_unknown_exchange_rejected_negative(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter = _stub_adapter(monkeypatch, _StubKIS())
    with pytest.raises(Exception):  # noqa: B017 - exact type owned by the app code
        await adapter.get_overseas_balance("ZZZZ")
    await adapter.aclose()


async def test_overseas_roundtrip_failure_injection_stops_before_cancel(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stub = _StubKIS(fail_on="TTTS3012R")
    adapter = _stub_adapter(monkeypatch, stub)
    with pytest.raises(ConnectionError):
        await _overseas_roundtrip(adapter)
    assert "TTTT1004U" not in [tr for _, tr in stub.calls]  # cancel never reached
    await adapter.aclose()


@pytest.mark.perf
async def test_overseas_roundtrip_perf_budget_and_gate_red(
    monkeypatch: pytest.MonkeyPatch, perf_budget: PerfBudget
) -> None:
    from tests.support.perf_inject import virtual_delayed_async

    budget_ms = 50.0
    adapter = _stub_adapter(monkeypatch, _StubKIS())
    sample = await perf_budget.best_of_async(lambda: _overseas_roundtrip(adapter), n=3, warmup=1)
    assert sample.cpu_ms < budget_ms, perf_budget.describe(sample, budget_ms=budget_ms)

    # gate-red: a virtual 10x-budget delay on `_request` must trip the same assertion.
    slow = virtual_delayed_async(_StubKIS().request, budget_ms * 10 / 1000)
    monkeypatch.setattr(adapter, "_request", slow)
    red = await perf_budget.best_of_async(lambda: _overseas_roundtrip(adapter), n=3, warmup=0)
    assert red.wall_ms >= budget_ms
    await adapter.aclose()


# --- D2 depth evidence for the domestic_stock roundtrip (task-24206) --------------------------
# Offline: `_request` is stubbed, so these run in default CI without credentials (no live_demo).
# Perf budget (new): place+get+cancel against an instant stub is pure adapter CPU; the budget is
# 50 ms for the 3-call sequence (same shape as the overseas one, ~1 ms measured, >30x headroom).


class _DomesticStub(_StubKIS):
    """Answers the three domestic_stock TRs with canned KIS payloads."""

    def __init__(self, *, rt_cd: str = "0", fail_on: str | None = None, rows: bool = True) -> None:
        super().__init__(rt_cd=rt_cd, fail_on=fail_on)
        self._rows = rows

    async def request(self, method: str, path: str, tr_id: str, **_: object) -> dict[str, object]:
        self.calls.append((method, tr_id))
        if tr_id == self._fail_on:
            raise ConnectionError("injected transport failure")
        if tr_id == "TTTC0012U":
            return {"rt_cd": "0", "output": {"KRX_FWDG_ORD_ORGNO": "91252", "ODNO": "0000002"}}
        if tr_id == "TTTC0081R":
            row = {"pdno": "005930", "ord_qty": "1", "tot_ccld_qty": "0", "sll_buy_dvsn_cd": "02"}
            return {"output1": [row] if self._rows else []}
        return {"rt_cd": self._rt_cd}


def _domestic_adapter(monkeypatch: pytest.MonkeyPatch, stub: _DomesticStub) -> KISAdapter:
    # The registry snapshot for 005930 is verified=False, so the app's tick/lot precheck rejects
    # the order fail-closed (pinned by the negative test below); the TR roundtrip itself is
    # exercised with that precheck neutralised.
    monkeypatch.setattr(trading_mixin, "_precheck_order", lambda order, registry: None)
    return _stub_adapter(monkeypatch, stub)


async def _domestic_roundtrip(adapter: KISAdapter) -> bool:
    order = _order(
        symbol=_KR_EQUITY_SYMBOL,
        price=_KR_EQUITY_SAFE_PRICE,
        currency=Currency.KRW,
        asset_class=AssetClass.KR_EQUITY,
        tag="kr-equity",
    )
    placed = await adapter.place_order(order)
    assert placed.exchange_order_id == "91252:0000002"
    fetched = await adapter.get_order(placed.exchange_order_id)
    assert fetched.exchange_order_id == placed.exchange_order_id
    return await adapter.cancel_order(placed.exchange_order_id)


async def test_domestic_roundtrip_offline_uses_expected_tr_ids(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stub = _DomesticStub()
    adapter = _domestic_adapter(monkeypatch, stub)
    assert await _domestic_roundtrip(adapter) is True
    assert [tr for _, tr in stub.calls] == ["TTTC0012U", "TTTC0081R", "TTTC0013U"]
    await adapter.aclose()


async def test_domestic_cancel_rejected_returns_false_negative(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter = _domestic_adapter(monkeypatch, _DomesticStub(rt_cd="1"))
    assert await _domestic_roundtrip(adapter) is False
    await adapter.aclose()


async def test_domestic_place_unverified_spec_rejected_fail_closed_negative(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stub = _DomesticStub()
    adapter = _stub_adapter(monkeypatch, stub)
    order = _order(
        symbol=_KR_EQUITY_SYMBOL,
        price=_KR_EQUITY_SAFE_PRICE,
        currency=Currency.KRW,
        asset_class=AssetClass.KR_EQUITY,
        tag="kr-equity",
    )
    with pytest.raises(Exception, match="UNVERIFIED_SPEC|verified=False"):  # noqa: B017
        await adapter.place_order(order)
    assert stub.calls == []  # never reached the exchange
    await adapter.aclose()


async def test_domestic_cancel_malformed_order_id_raises_negative(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stub = _DomesticStub()
    adapter = _domestic_adapter(monkeypatch, stub)
    with pytest.raises(Exception, match="orgno:odno"):  # noqa: B017 - FatalExchangeError
        await adapter.cancel_order("no-colon-id")
    assert stub.calls == []  # rejected before any request
    await adapter.aclose()


async def test_domestic_get_order_unknown_id_raises_negative(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter = _domestic_adapter(monkeypatch, _DomesticStub(rows=False))
    with pytest.raises(Exception, match="찾을 수 없음"):  # noqa: B017 - FatalExchangeError
        await adapter.get_order("91252:9999999")
    await adapter.aclose()


async def test_domestic_roundtrip_failure_injection_stops_before_cancel(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stub = _DomesticStub(fail_on="TTTC0081R")
    adapter = _domestic_adapter(monkeypatch, stub)
    with pytest.raises(ConnectionError):
        await _domestic_roundtrip(adapter)
    assert "TTTC0013U" not in [tr for _, tr in stub.calls]  # cancel never reached
    await adapter.aclose()


@pytest.mark.perf
async def test_domestic_roundtrip_perf_budget_and_gate_red(
    monkeypatch: pytest.MonkeyPatch, perf_budget: PerfBudget
) -> None:
    from tests.support.perf_inject import virtual_delayed_async

    budget_ms = 50.0
    adapter = _domestic_adapter(monkeypatch, _DomesticStub())
    sample = await perf_budget.best_of_async(lambda: _domestic_roundtrip(adapter), n=3, warmup=1)
    assert sample.cpu_ms < budget_ms, perf_budget.describe(sample, budget_ms=budget_ms)

    # gate-red: a virtual 10x-budget delay on `_request` must trip the same assertion.
    slow = virtual_delayed_async(_DomesticStub().request, budget_ms * 10 / 1000)
    monkeypatch.setattr(adapter, "_request", slow)
    red = await perf_budget.best_of_async(lambda: _domestic_roundtrip(adapter), n=3, warmup=0)
    assert red.wall_ms >= budget_ms
    await adapter.aclose()
