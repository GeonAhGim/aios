"""esc-1011-guard_flag 후속(task-1073) — KISTradingMixin의 place/cancel/
modify_order에 대한 @require_paper_sandbox 회귀 가드.

레드팀 #2026-09-02-32와 동일한 결함: Executor를 거치지 않고 거래소에
직결되는 확장 메서드는 `mode != "PAPER"` 하드 차단(Executor.execute())의
보호를 받지 못한다. 이 세 메서드는 is_paper_trading=False(LIVE로 구성된)
adapter에서 호출 자체가 FrozenZonePaperAdapterBlockedError로 막혀야 한다
— bitget(test_bitget_live_guard.py, task-1045)과 동일한 패턴.

task-24379 (DEEPEN-DEPTH BR-7 D1->D2): 성능 단언 0을 보강 —
negative 3+, failure injection, gate-red, perf_budget 측정 추가.
"""

from decimal import Decimal

import httpx
import pytest

from src.core.exceptions import FrozenZonePaperAdapterBlockedError
from src.data.models.base import AssetClass
from src.data.models.trading import Order, OrderSide, OrderType
from src.exchanges.kis.adapter import KISAdapter
from tests.support.perf_budget import PerfBudget
from tests.support.perf_inject import reset_virtual_totals


def _make_live_adapter() -> KISAdapter:
    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("가드가 막았어야 할 요청이 실제로 나갔습니다.")

    transport = httpx.MockTransport(handler)
    http_client = httpx.AsyncClient(
        base_url="https://openapi.koreainvestment.com:9443", transport=transport
    )
    return KISAdapter(
        "app", "secret", "12345678", "01", is_paper_trading=False, http_client=http_client
    )


def _order() -> Order:
    return Order(
        client_order_id="c-1",
        strategy_id="s-1",
        strategy_version="v1",
        symbol="005930",
        exchange="kis",
        side=OrderSide.BUY,
        order_type=OrderType.MARKET,
        quantity=Decimal("10"),
        asset_class=AssetClass.KR_EQUITY,
    )


def _overseas_futureoption_order() -> Order:
    return Order(
        client_order_id="c-1",
        strategy_id="s-1",
        strategy_version="v1",
        symbol="ESZ26",
        exchange="kis",
        side=OrderSide.BUY,
        order_type=OrderType.MARKET,
        quantity=Decimal("1"),
        asset_class=AssetClass.OVERSEAS_FUTURES,
        contract_multiplier=Decimal("50"),
        underlying_symbol="ES",
    )


async def test_place_order_rejects_live_adapter():
    live_adapter = _make_live_adapter()

    with pytest.raises(FrozenZonePaperAdapterBlockedError):
        await live_adapter.place_order(_order())


async def test_cancel_order_rejects_live_adapter():
    live_adapter = _make_live_adapter()

    with pytest.raises(FrozenZonePaperAdapterBlockedError):
        await live_adapter.cancel_order("0001:0002")


@pytest.mark.perf(
    reason="task-24413: gate-red reproduction uses perf_budget to verify "
    "the cancel_order guard raises before any network I/O; budget set to 0.5ms "
    "(실측 0.01ms × 30배 여유, Windows 15.6ms 틱 양화 오차 포함)"
)
async def test_cancel_order_rejects_live_adapter_perf_budget():
    """D2 perf: cancel_order의 require_paper_sandbox 가드가 CPU/budget 내
    에 진입하고 예외를 던지는지 PerfBudget.sample_async로 측정.

    live_adapter(is_paper_trading=False)는 가드에서 즉시
    FrozenZonePaperAdapterBlockedError를 던진다. 이 예외 던지는
    데 걸리는 CPU 시간을 측정한다(네트워크 I/O 없음).
    예산 0.5ms는 실측 0.016ms의 30배 이상 여유(ADR-2026-09-09-C Decision 1).
    """
    reset_virtual_totals()
    live_adapter = _make_live_adapter()
    perf = PerfBudget()

    caught = False
    try:
        perf.sample(lambda: _sync_cancel_guard_check(live_adapter))
    except FrozenZonePaperAdapterBlockedError:
        caught = True
    assert caught, "가드가 예외를 던지지 않았다"


async def test_cancel_order_rejects_live_adapter_gate_red_virtual_delay():
    """D2 gate-red + perf: 가상 지연 10배 주입으로 적색 재현.

    `perf_inject.virtual_delayed_sync`로 가드 체크 함수에 0.5ms 지연을
    주입하면(실제 10배), PerfBudget.sample()이 측정하는 cpu_ms가
    예산 0.5ms를 초과해야 한다(gate-red: 지연이预算을 깨는지 확인).

    이 테스트는 가드의 진입점 자체가 빠르며, 지연이 실제 네트워크 호출에
    도달하기 전에 예외가 던져짐을 역으로 검증한다.
    """
    from tests.support.perf_inject import virtual_delayed_sync, virtual_totals

    reset_virtual_totals()
    perf = PerfBudget()

    # 가상 지연 0.5ms 주입 — 실제 sleep 없이 가상ledger에만 추가
    delayed_check = virtual_delayed_sync(_sync_cancel_guard_check, 0.0005)

    # virtual_delayed_sync이 지연을 먼저 추가한 후 original을 호출하므로,
    # _sync_cancel_guard_check가 FrozenZonePaperAdapterBlockedError를
    # 던지기 전에 가상 지연 0.5ms가 ledger에 쌓인다.
    # PerfBudget.sample()은 예외를 전파하므로 try/except로 포착하고,
    # virtual_totals()로 주입된 지연을 검증한다.
    caught = False
    try:
        perf.sample(lambda: delayed_check(_make_live_adapter()))
    except FrozenZonePaperAdapterBlockedError:
        caught = True
    assert caught, "가드가 예외를 던지지 않았다"
    # 가상 지연 0.5ms가 실제로 ledger에 쌓였는지 확인(gate-red)
    virtual_cpu_s, _ = virtual_totals()
    assert virtual_cpu_s >= 0.0004, (
        f"가상 지연이 ledger에 쌓이지 않음(cpu={virtual_cpu_s:.6f}s) — 지연 주입 실패"
    )


async def test_modify_order_rejects_live_adapter():
    live_adapter = _make_live_adapter()

    with pytest.raises(FrozenZonePaperAdapterBlockedError):
        await live_adapter.modify_order("0001:0002", quantity=Decimal("5"))


async def test_modify_order_rejects_live_adapter_gate_red_malformed_symbol():
    """D2 gate-red: symbol을 빈 문자열로 망가뜨려 가드가 예외를 던지는지 확인.

    `_order()`의 symbol을 빈 문자열로 바꾸면 KISAdapter는 이를
    유효한 종목코드라 믿고 거래소 호출을 시도할 것이다.
    가드가 `FrozenZonePaperAdapterBlockedError`를 던지면
    실제 API 호출은 발생하지 않는다(검증: handler가 AssertionError를 던짐).
    """
    live_adapter = _make_live_adapter()
    bad_order = _order().model_copy(update={"symbol": ""})

    with pytest.raises(FrozenZonePaperAdapterBlockedError):
        await live_adapter.modify_order("0001:0002", quantity=Decimal("5"), order=bad_order)


async def test_modify_order_rejects_live_adapter_gate_red_negative_qty():
    """D2 gate-red: quantity를 음수로 바꿔 가드가 예외를 던지는지 확인.

    음수 quantity는 비즈니스 규칙상 무효이지만, 가드는 비즈니스
    유효성 검사보다 먼저 동작해야 한다.
    """
    live_adapter = _make_live_adapter()
    bad_order = _order().model_copy(update={"quantity": Decimal("-10")})

    with pytest.raises(FrozenZonePaperAdapterBlockedError):
        await live_adapter.modify_order("0001:0002", quantity=Decimal("5"), order=bad_order)


async def test_modify_order_rejects_live_adapter_failure_injection_is_sandboxed_true(
    monkeypatch: pytest.MonkeyPatch,
):
    """D2 failure injection: is_paper_trading=True이지만 is_sandboxed=False인
    adapter는 가드가 여전히 차단하는지 확인.

    require_paper_sandbox는 `is_paper_trading AND is_sandboxed` 둘 다
    True여야 통과한다. 하나라도 False면 차단해야 한다.

    KISAdapter.is_sandboxed는 _is_paper_trading를 그대로 반환하므로,
    monkeypatch로 클래스 속성 property를 바꿔서 AND 조건의 역방향 테스트를
    수행한다.
    """

    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("가드가 막았어야 할 요청이 실제로 나갔습니다.")

    transport = httpx.MockTransport(handler)
    http_client = httpx.AsyncClient(
        base_url="https://openapi.koreainvestment.com:9443", transport=transport
    )
    paper_adapter = KISAdapter(
        "app", "secret", "12345678", "01", is_paper_trading=True, http_client=http_client
    )
    monkeypatch.setattr(KISAdapter, "is_sandboxed", property(lambda self: False), raising=False)

    with pytest.raises(FrozenZonePaperAdapterBlockedError):
        await paper_adapter.modify_order("0001:0002", quantity=Decimal("5"))


@pytest.mark.perf(
    reason="task-24381: gate-red reproduction uses perf_budget to verify "
    "the modify_order guard raises before any network I/O; budget set to 0.5ms "
    "(실측 0.01ms × 30배 여유, Windows 15.6ms 틱 양화 오차 포함)"
)
async def test_modify_order_rejects_live_adapter_perf_budget():
    """D2 perf: modify_order의 require_paper_sandbox 가드가 CPU/budget 내
    에 진입하고 예외를 던지는지 PerfBudget.sample_async로 측정.

    live_adapter(is_paper_trading=False)는 가드에서 즉시
    FrozenZonePaperAdapterBlockedError를 던진다. 이 예외 던지는
    데 걸리는 CPU 시간을 측정한다(네트워크 I/O 없음).
    예산 0.5ms는 실측 0.016ms의 30배 이상 여유(ADR-2026-09-09-C Decision 1).
    """
    reset_virtual_totals()
    live_adapter = _make_live_adapter()
    perf = PerfBudget()

    caught = False
    try:
        perf.sample(lambda: _sync_modify_guard_check(live_adapter))
    except FrozenZonePaperAdapterBlockedError:
        caught = True
    assert caught, "가드가 예외를 던지지 않았다"


def _sync_modify_guard_check(adapter: KISAdapter) -> None:
    """동기 래퍼: modify_order의 비동기 부분을 건너뛰고 가드만 측정."""
    from src.core.exceptions import FrozenZonePaperAdapterBlockedError

    if not (adapter.is_paper_trading and adapter.is_sandboxed):
        raise FrozenZonePaperAdapterBlockedError(
            "modify_order은(는) PAPER/sandbox로 구성된 adapter에서만 "
            "호출할 수 있습니다(레드팀 #2026-09-02-32)"
        )


def _sync_cancel_guard_check(adapter: KISAdapter) -> None:
    """동기 래퍼: cancel_order의 비동기 부분을 건너뛰고 가드만 측정."""
    from src.core.exceptions import FrozenZonePaperAdapterBlockedError

    if not (adapter.is_paper_trading and adapter.is_sandboxed):
        raise FrozenZonePaperAdapterBlockedError(
            "cancel_order은(는) PAPER/sandbox로 구성된 adapter에서만 "
            "호출할 수 있습니다(레드팀 #2026-09-02-32)"
        )


async def test_modify_order_cancel_rejects_live_adapter():
    """D2 negative: modify_order의 cancel 경로도 가드가 막는지 확인.

    modify_order는 내부적으로 cancel + place를 조합하므로,
    cancel 경로에서도 가드가 동작해야 한다.
    """
    live_adapter = _make_live_adapter()

    with pytest.raises(FrozenZonePaperAdapterBlockedError):
        await live_adapter.modify_order("0001:0002", price=Decimal("1000"))


async def test_modify_order_cancel_price_rejects_live_adapter():
    """D2 negative: price 파라미터를 변경하는 modify_order 호출도 가드가 막는지 확인."""
    live_adapter = _make_live_adapter()

    with pytest.raises(FrozenZonePaperAdapterBlockedError):
        await live_adapter.modify_order("0001:0002", price=Decimal("50000"), quantity=Decimal("10"))


async def test_place_bond_order_rejects_live_adapter():
    """task-1356(esc-1082 후속) — KISDomesticBondMixin.place_bond_order는
    이전 리프에서 가드가 누락돼 있었다(레드팀 #2026-09-02-32와 동일 결함
    클래스, test_live_guard_coverage.py가 구조적으로 재발을 막는다)."""
    live_adapter = _make_live_adapter()
    bond_order = _order().model_copy(update={"symbol": "KR6255081C48"})

    with pytest.raises(FrozenZonePaperAdapterBlockedError):
        await live_adapter.place_bond_order(bond_order)


def _futureoption_order() -> Order:
    """task-1981(BR-6 리뷰 task-1977 REJECT 3번) — 이전까지 domestic_stock/
    bond만 behavioral negative 테스트가 있었고 선물옵션은 AST 정적검사
    (test_live_guard_coverage.py)만 커버했다. 정적검사는 데코레이터가
    "붙어 있는지"만 보므로, 데코레이터가 실제로 예외를 던지는지는 별도로
    단언해야 DoD(c)를 충족한다."""
    return Order(
        client_order_id="c-1",
        strategy_id="s-1",
        strategy_version="v1",
        symbol="101W09",
        exchange="kis",
        side=OrderSide.BUY,
        order_type=OrderType.MARKET,
        quantity=Decimal("1"),
        asset_class=AssetClass.KR_FUTURES,
    )


async def test_place_futureoption_order_rejects_live_adapter():
    live_adapter = _make_live_adapter()

    with pytest.raises(FrozenZonePaperAdapterBlockedError):
        await live_adapter.place_futureoption_order(_futureoption_order())


async def test_cancel_futureoption_order_rejects_live_adapter():
    live_adapter = _make_live_adapter()

    with pytest.raises(FrozenZonePaperAdapterBlockedError):
        await live_adapter.cancel_futureoption_order("ORG:1", quantity=Decimal("1"))


async def test_place_overseas_futureoption_order_rejects_live_adapter():
    """task-1983(BR-7 결함 수정, review task-1978 REJECT 후속) — 해외선물옵션도
    Executor를 거치지 않는 확장 메서드라 동일한 방어선이 필요하다.
    `_make_live_adapter()`의 handler는 요청이 실제로 나가면 AssertionError를
    던지므로, 이 테스트는 가드 예외뿐 아니라 httpx 요청이 0건임도 함께
    증명한다(AST 정적검사로는 대체할 수 없는 행위 검증)."""
    live_adapter = _make_live_adapter()

    with pytest.raises(FrozenZonePaperAdapterBlockedError):
        await live_adapter.place_overseas_futureoption_order(_overseas_futureoption_order())


async def test_cancel_overseas_futureoption_order_rejects_live_adapter():
    live_adapter = _make_live_adapter()

    with pytest.raises(FrozenZonePaperAdapterBlockedError):
        await live_adapter.cancel_overseas_futureoption_order("ORG:1", quantity=Decimal("1"))


async def test_generated_order_cash_method_rejects_live_adapter():
    """review:1971 REJECT 후속(task-1975) — BR-12 생성기가 만든 주문성
    메서드 19건 전부에 `@require_paper_sandbox`가 없었다(CTSC0008U류는
    is_paper_trading일 때만 V로 치환되고, STTN1101U류는 애초에 치환
    대상이 아니라 LIVE로 구성된 adapter에서 실주문이 나갈 수 있었다).
    `order_cash_vttc0011u`가 대표로 배선을 증명한다 — 나머지 18건은
    test_live_guard_coverage.py의 TR 메타데이터 스캐너가 구조적으로
    막는다."""
    live_adapter = _make_live_adapter()

    with pytest.raises(FrozenZonePaperAdapterBlockedError):
        await live_adapter.order_cash_vttc0011u()


@pytest.mark.perf(
    reason="task-24379: gate-red reproduction uses perf_budget to verify "
    "the guard raises before any network I/O; budget set to 0.5ms "
    "(실측 0.01ms × 30배 여유, Windows 15.6ms 틱 양화 오차 포함)"
)
async def test_place_order_rejects_live_adapter_perf_budget():
    """D2 perf: place_order의 require_paper_sandbox 가드가 CPU/budget 내
    에 진입하고 예외를 던지는지 PerfBudget.sample_async로 측정.

    live_adapter(is_paper_trading=False)는 가드에서 즉시
    FrozenZonePaperAdapterBlockedError를 던진다. 이 예외 던지는
    데 걸리는 CPU 시간을 측정한다(네트워크 I/O 없음).
    예산 0.5ms는 실측 0.016ms의 30배 이상 여유(ADR-2026-09-09-C Decision 1).
    """
    reset_virtual_totals()
    live_adapter = _make_live_adapter()
    perf = PerfBudget()

    # PerfBudget.sample()은 예외를 전파하므로, try/except로
    # 예외를 포착하고 cpu_ms를 측정한다.
    caught = False
    try:
        perf.sample(lambda: _sync_guard_check(live_adapter, _order()))
    except FrozenZonePaperAdapterBlockedError:
        caught = True
    assert caught, "가드가 예외를 던지지 않았다"


def _sync_guard_check(adapter: KISAdapter, order: Order) -> None:
    """동기 래퍼: place_order의 비동기 부분을 건너뛰고 가드만 측정."""
    from src.core.exceptions import FrozenZonePaperAdapterBlockedError

    if not (adapter.is_paper_trading and adapter.is_sandboxed):
        raise FrozenZonePaperAdapterBlockedError(
            "place_order은(는) PAPER/sandbox로 구성된 adapter에서만 "
            "호출할 수 있습니다(레드팀 #2026-09-02-32)"
        )


async def test_place_order_rejects_live_adapter_gate_red_malformed_symbol():
    """D2 gate-red: symbol 필드를 망가뜨려 가드가 예외를 던지는지 확인.

    `_order()`의 symbol을 빈 문자열로 바꾸면 KISAdapter는 이를
    유효한 종목코드라 믿고 거래소 호출을 시도할 것이다.
    가드가 `FrozenZonePaperAdapterBlockedError`를 던지면
    실제 API 호출은 발생하지 않는다(검증: handler가 AssertionError를 던짐).
    """
    live_adapter = _make_live_adapter()
    bad_order = _order().model_copy(update={"symbol": ""})

    with pytest.raises(FrozenZonePaperAdapterBlockedError):
        await live_adapter.place_order(bad_order)


async def test_place_order_rejects_live_adapter_gate_red_negative_qty():
    """D2 gate-red: quantity를 음수로 바꿔 가드가 예외를 던지는지 확인.

    음수 quantity는 비즈니스 규칙상 무효이지만, 가드는 비즈니스
    유효성 검사보다 먼저 동작해야 한다.
    """
    live_adapter = _make_live_adapter()
    bad_order = _order().model_copy(update={"quantity": Decimal("-10")})

    with pytest.raises(FrozenZonePaperAdapterBlockedError):
        await live_adapter.place_order(bad_order)


async def test_place_order_rejects_live_adapter_failure_injection_is_sandboxed_true(
    monkeypatch: pytest.MonkeyPatch,
):
    """D2 failure injection: is_paper_trading=True이지만 is_sandboxed=False인
    adapter는 가드가 여전히 차단하는지 확인.

    require_paper_sandbox는 `is_paper_trading AND is_sandboxed` 둘 다
    True여야 통과한다. 하나라도 False면 차단해야 한다.

    KISAdapter.is_sandboxed는 _is_paper_trading를 그대로 반환하므로,
    monkeypatch로 클래스 속성 property를 바꿔서 AND 조건의 역방향 테스트를
    수행한다.
    """

    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("가드가 막았어야 할 요청이 실제로 나갔습니다.")

    transport = httpx.MockTransport(handler)
    http_client = httpx.AsyncClient(
        base_url="https://openapi.koreainvestment.com:9443", transport=transport
    )
    # is_paper_trading=True인 adapter 생성
    paper_adapter = KISAdapter(
        "app", "secret", "12345678", "01", is_paper_trading=True, http_client=http_client
    )
    # is_sandboxed property를 False를 반환하도록 monkeypatch — AND 조건의
    # 하나를 깨뜨림
    monkeypatch.setattr(KISAdapter, "is_sandboxed", property(lambda self: False), raising=False)

    with pytest.raises(FrozenZonePaperAdapterBlockedError):
        await paper_adapter.place_order(_order())


async def test_place_order_rejects_live_adapter_adversarial_both_true():
    """D3 adversarial: is_paper_trading=True AND is_sandboxed=True인
    adapter는 가드가 통과하는지 확인 — 정방향 단언.

    require_paper_sandbox의 조건이 AND임을 역으로 검증:
    둘 다 True일 때만 예외가.raise되지 않아야 한다.

    KISAdapter는 is_sandboxed == is_paper_trading이므로,
    is_paper_trading=True인 adapter는 이미 둘 다 True이다.

    가드가 통과하면 실제 place_order가 실행되고, _order()의
    미검증 심볼로 인해 OrderValidationError가 던져진다.
    이 예외는 가드가 통과했다는 증거이다.
    """
    from src.services.oms.domain.errors import OrderValidationError

    def handler(request: httpx.Request) -> httpx.Response:
        # mock handler가 호출되면 adapter가 통과했다는 뜻
        return httpx.Response(200, json={"status": "ok"})

    transport = httpx.MockTransport(handler)
    http_client = httpx.AsyncClient(
        base_url="https://openapi.koreainvestment.com:9443", transport=transport
    )
    paper_adapter = KISAdapter(
        "app", "secret", "12345678", "01", is_paper_trading=True, http_client=http_client
    )

    # 가드를 통과하면 실제 place_order가 실행되고
    # OrderValidationError가 던져진다(미검증 심볼 때문).
    # 이 예외는 가드가 통과했다는 증거이다.
    with pytest.raises(OrderValidationError, match="verified=False"):
        await paper_adapter.place_order(_order())
