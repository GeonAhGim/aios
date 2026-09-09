"""esc-1082-qa_finding 후속(task-1356) — src/exchanges/**의 모든 주문성
async 메서드가 예외 없이 `@require_paper_sandbox`를 갖는지 AST로 검증한다.

레드팀 #2026-09-02-32/esc-1011/esc-1032와 같은 결함 클래스: 신규 확장
메서드(place_bond_order 등)가 추가될 때마다 사람이 데코레이터를 붙이는
걸 잊는 사고가 반복됐다. `meta/guards/security_guard.py`의
FUND_MOVING_METHODS 정규식은 meta 저장소(사람만 수정) 소관이라 이
저장소 안에서 직접 보강할 수 없다 — 이 테스트가 저장소 내부의 대체
회귀 방어선이다.

정규식 문자열 매칭이 아니라 `ast.walk` + `decorator_list` 검사로 구현한다
(문자열 검사는 데코레이터가 별칭 import되거나 조건부로 적용된 경우를
놓칠 수 있다 — decorator_list는 실제 파서 산출물이라 그런 우회가 없다).

review:1971 REJECT 후속(task-1975) — `_FUND_MOVING_NAME`은 "동사가 이름
앞에 오는" 명명 규칙에 의존한다. `scripts/kis_generate_adapters.py`가
기계 생성하는 `order_cash_vttc0011u` 같은 이름은 이 규칙을 따르지
않아 19건이 통과됐다(레드팀 #2026-09-02-32/esc-1082/I-10과 같은 결함
클래스). 이름 규칙은 명명 드리프트에 fail-open하므로, 판별을 이름이
아니라 `docs/design/kis_tr_reference.json`의 TR 메타데이터(method=POST
=주문·정정·취소)로도 병행한다 — 두 판별식의 합집합이 위반 여부를
결정한다(화이트리스트 확장이 아니라 판별식 자체를 넓히는 방식).

esc-2514 guard flag 후속(task-2530) — `_FUND_MOVING_NAME`은 주문/포지션
계열 동사만 다뤄 `BitgetAccountMixin.transfer`(계정 내부 자금 이체)
같은 이체 계열 메서드를 놓쳤다. 판별식에 `transfer`/`withdraw` 이름
패턴을 추가한다 — 이름 규칙 하나에 fail-open하는 결함 클래스를
반복하지 않도록, 이번에도 화이트리스트가 아니라 판별식 자체를 넓힌다.
"""
from __future__ import annotations

import ast
import json
import re
from decimal import Decimal
from pathlib import Path

import httpx
import pytest

from src.core.exceptions import FrozenZonePaperAdapterBlockedError
from src.exchanges.bitget.adapter import BitgetAdapter
from src.exchanges.nh.adapter import NHAdapter

_REPO_ROOT = Path(__file__).resolve().parents[3]
_SRC_EXCHANGES = _REPO_ROOT / "src" / "exchanges"
_KIS_TR_REFERENCE = _REPO_ROOT / "docs" / "design" / "kis_tr_reference.json"

_FUND_MOVING_NAME = re.compile(
    r"^(place|cancel|modify|amend|close|submit)_\w*(order|orders|position|tpsl)\w*$"
    r"|^\w*(transfer|withdraw)\w*$",
    re.IGNORECASE,
)

_GUARD_DECORATOR_NAME = "require_paper_sandbox"

# 예외 화이트리스트 — 이유를 명시한 상수로 관리한다. 이번 리프에서는
# 비워 둔다(레드팀 지적 결함 클래스를 새 예외로 재도입하지 않는다).
_WHITELIST: frozenset[str] = frozenset()


def _load_kis_order_tr_ids() -> frozenset[str]:
    """`kis_tr_reference.json`에서 method=POST인 TR ID 전부(소문자) —
    이 기준 목록 안에서 POST는 예외 없이 매수/매도/정정/취소/예약주문이다
    (레이블에 "주문" 포함, `scripts/kis_generate_adapters.is_order_method`와
    동일 판정을 스캐너 쪽에서 독립적으로 재계산한다 — 생성기 로직 자체의
    버그도 잡아내려면 생성기 함수를 재사용하지 않는 쪽이 안전하다)."""
    data = json.loads(_KIS_TR_REFERENCE.read_text(encoding="utf-8"))
    return frozenset(row["tr_id"].lower() for row in data["trs"] if row["method"] == "POST")


def _matches_kis_order_tr_id(name: str, order_tr_ids: frozenset[str]) -> bool:
    """생성기(`_method_name`)는 `{slug}_{tr_id.lower()}` 형태로 메서드명을
    짓는다 — TR ID가 이름 끝에 밑줄로 구분돼 붙어 있으면 주문성으로 본다."""
    return any(name == tr_id or name.endswith(f"_{tr_id}") for tr_id in order_tr_ids)


def _is_stub_body(body: list[ast.stmt]) -> bool:
    """`ExchangeAdapter`(ABC)/`Protocol` 선언부처럼 실제 구현이 없고
    (선택적 docstring 뒤에) `...`만 있는 함수는 가드를 붙일 대상이
    아니다 — 호출 가능한 코드가 없다."""
    stmts = list(body)
    if stmts and isinstance(stmts[0], ast.Expr) and isinstance(
        stmts[0].value, ast.Constant
    ) and isinstance(stmts[0].value.value, str):
        stmts = stmts[1:]  # docstring 제외
    if len(stmts) != 1:
        return False
    stmt = stmts[0]
    return (
        isinstance(stmt, ast.Expr)
        and isinstance(stmt.value, ast.Constant)
        and stmt.value.value is Ellipsis
    )


def _has_guard_decorator(decorator_list: list[ast.expr]) -> bool:
    for dec in decorator_list:
        node = dec
        if isinstance(node, ast.Call):
            node = node.func
        if isinstance(node, ast.Name) and node.id == _GUARD_DECORATOR_NAME:
            return True
        if isinstance(node, ast.Attribute) and node.attr == _GUARD_DECORATOR_NAME:
            return True
    return False


def _find_unguarded_fund_moving_methods() -> list[str]:
    order_tr_ids = _load_kis_order_tr_ids()
    violations: list[str] = []
    for path in sorted(_SRC_EXCHANGES.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.AsyncFunctionDef):
                continue
            is_fund_moving = _FUND_MOVING_NAME.match(node.name) or _matches_kis_order_tr_id(
                node.name, order_tr_ids
            )
            if not is_fund_moving:
                continue
            if _is_stub_body(node.body):
                continue
            qualname = f"{path.relative_to(_SRC_EXCHANGES.parents[1])}:{node.lineno}:{node.name}"
            if qualname in _WHITELIST:
                continue
            if not _has_guard_decorator(node.decorator_list):
                violations.append(qualname)
    return violations


def test_all_fund_moving_methods_have_paper_sandbox_guard():
    violations = _find_unguarded_fund_moving_methods()
    assert not violations, (
        "다음 주문성 메서드에 @require_paper_sandbox가 없습니다"
        "(레드팀 #2026-09-02-32와 동일 결함 클래스):\n"
        + "\n".join(violations)
    )


def test_scanner_actually_detects_missing_decorator():
    """스캐너 자체의 회귀 방지 — 가드가 없는 메서드를 만들면 실제로
    잡아내는지 확인한다(거짓 초록 방지)."""
    src = (
        "class X:\n"
        "    async def cancel_order(self, order_id):\n"
        "        return True\n"
    )
    tree = ast.parse(src)
    node = next(n for n in ast.walk(tree) if isinstance(n, ast.AsyncFunctionDef))
    assert _FUND_MOVING_NAME.match(node.name)
    assert not _is_stub_body(node.body)
    assert not _has_guard_decorator(node.decorator_list)


def test_scanner_detects_generator_style_name_via_tr_metadata():
    """review:1971 REJECT 후속(task-1975) — `order_cash_vttc0011u`처럼
    동사가 이름 앞에 오지 않는 생성기 명명 규칙은 `_FUND_MOVING_NAME`을
    통과하지만, TR 메타데이터(VTTC0011U는 method=POST) 판별로는 잡혀야
    한다. 이름 규칙 하나에만 기댄 스캐너가 이 클래스의 이름을 놓쳤던
    결함(BR-12 19건 무방비)의 회귀 방지 테스트."""
    order_tr_ids = _load_kis_order_tr_ids()
    assert "vttc0011u" in order_tr_ids
    name = "order_cash_vttc0011u"
    assert not _FUND_MOVING_NAME.match(name)
    assert _matches_kis_order_tr_id(name, order_tr_ids)


def test_scanner_detects_transfer_style_fund_moving_names():
    """esc-2514(task-2530) — `_FUND_MOVING_NAME`이 이체 계열 이름
    (`transfer`/`transfer_broker_subaccount`/`withdraw_to_address`)도
    잡아내는지 고정한다. 이 판별이 없으면 account_mixin.py::transfer의
    `@require_paper_sandbox`를 지워도 전체 스캔 테스트가 FAIL 하지
    않는다(DoD (c))."""
    assert _FUND_MOVING_NAME.match("transfer")
    assert _FUND_MOVING_NAME.match("transfer_broker_subaccount")
    assert _FUND_MOVING_NAME.match("transfer_to_subaccount")
    assert _FUND_MOVING_NAME.match("withdraw_to_address")
    assert not _FUND_MOVING_NAME.match("get_convert_history")


def test_scanner_ignores_abstract_stub_methods():
    """`src/exchanges/common/adapter.py`의 ABC 선언부(본문이 docstring +
    `...` 또는 `...`뿐)는 실제 구현이 아니므로 스캐너가 건너뛰어야 한다."""
    src = (
        "class X:\n"
        "    async def place_order(self, order):\n"
        "        '''docstring'''\n"
        "        ...\n"
        "    async def cancel_order(self, order_id): ...\n"
    )
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, ast.AsyncFunctionDef):
            assert _is_stub_body(node.body)


def _make_live_nh_adapter() -> NHAdapter:
    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("가드가 막았어야 할 요청이 실제로 나갔습니다.")

    transport = httpx.MockTransport(handler)
    http_client = httpx.AsyncClient(base_url="https://api.nhplug.com", transport=transport)
    return NHAdapter(
        "app", "secret", "12345678", is_paper_trading=True, http_client=http_client
    )


async def test_nh_place_order_rejects_adapter():
    """decision — NH는 is_paper_trading/is_sandboxed가 항상 False다(공식
    포털에 모의투자 미제공, task-106 확인). 따라서 생성자 인자와 무관하게
    place/cancel/modify_order는 항상 차단되는 게 의도된 fail-closed
    결과다 — 버그가 아니다."""
    adapter = _make_live_nh_adapter()
    with pytest.raises(FrozenZonePaperAdapterBlockedError):
        await adapter.place_order(_order_stub())


async def test_nh_cancel_order_rejects_adapter():
    adapter = _make_live_nh_adapter()
    with pytest.raises(FrozenZonePaperAdapterBlockedError):
        await adapter.cancel_order("005930:1")


async def test_nh_modify_order_rejects_adapter():
    adapter = _make_live_nh_adapter()
    with pytest.raises(FrozenZonePaperAdapterBlockedError):
        await adapter.modify_order("005930:1", price="100", size="1")


def _order_stub():
    from decimal import Decimal

    from src.data.models.base import AssetClass
    from src.data.models.trading import Order, OrderSide, OrderType

    return Order(
        client_order_id="c-1",
        strategy_id="s-1",
        strategy_version="v1",
        symbol="005930",
        exchange="nh",
        side=OrderSide.BUY,
        order_type=OrderType.MARKET,
        quantity=Decimal("10"),
        asset_class=AssetClass.KR_EQUITY,
    )


def _make_live_bitget_adapter() -> BitgetAdapter:
    """esc-2514(task-2530) — LIVE(demo_mode=False)로 구성된 BitgetAdapter.
    핸들러가 요청을 받으면 즉시 AssertionError — 가드가 실행 자체를
    막아야 하므로 이 핸들러가 호출되는 순간 이미 결함이다."""

    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("가드가 막았어야 할 요청이 실제로 나갔습니다.")

    transport = httpx.MockTransport(handler)
    http_client = httpx.AsyncClient(base_url="https://api.bitget.com", transport=transport)
    return BitgetAdapter("key", "secret", "passphrase", demo_mode=False, http_client=http_client)


def _make_paper_bitget_adapter(handler) -> BitgetAdapter:
    transport = httpx.MockTransport(handler)
    http_client = httpx.AsyncClient(base_url="https://api.bitget.com", transport=transport)
    return BitgetAdapter("key", "secret", "passphrase", demo_mode=True, http_client=http_client)


async def test_bitget_transfer_rejects_live_adapter():
    """esc-2514(task-2530) DoD (a)/(b) — account_mixin.py:transfer가
    `@require_paper_sandbox` 없이 LIVE adapter에서도 실행되던 결함의
    재현/회귀 테스트. 가드가 있으면 이 호출은 HTTP 요청이 나가기 전에
    FrozenZonePaperAdapterBlockedError로 거부된다."""
    live_adapter = _make_live_bitget_adapter()
    with pytest.raises(FrozenZonePaperAdapterBlockedError):
        await live_adapter.transfer("spot", "usdt_futures", Decimal("100"), "usdt")


async def test_bitget_transfer_allows_paper_adapter():
    """DoD (b) 양방향 단언 — PAPER/샌드박스 인스턴스에서는 기존 경로가
    그대로 동작해야 한다(무회귀)."""

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v2/spot/wallet/transfer"
        return httpx.Response(
            200, json={"code": "00000", "msg": "success", "requestTime": 1, "data": {}}
        )

    paper_adapter = _make_paper_bitget_adapter(handler)
    result = await paper_adapter.transfer("spot", "usdt_futures", Decimal("100"), "usdt")
    assert result is True


async def test_bitget_transfer_broker_subaccount_rejects_live_adapter():
    """esc-2514 스캔 확장으로 함께 발견된 동일 결함 클래스
    (`broker_mixin.py::transfer_broker_subaccount`)의 회귀 테스트."""
    live_adapter = _make_live_bitget_adapter()
    with pytest.raises(FrozenZonePaperAdapterBlockedError):
        await live_adapter.transfer_broker_subaccount("sub-1", "usdt", Decimal("10"))
