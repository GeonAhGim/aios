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
"""
from __future__ import annotations

import ast
import re
from pathlib import Path

import httpx
import pytest

from src.core.exceptions import FrozenZonePaperAdapterBlockedError
from src.exchanges.nh.adapter import NHAdapter

_SRC_EXCHANGES = Path(__file__).resolve().parents[3] / "src" / "exchanges"

_FUND_MOVING_NAME = re.compile(
    r"^(place|cancel|modify|amend|close|submit)_\w*(order|orders|position|tpsl)\w*$",
    re.IGNORECASE,
)

_GUARD_DECORATOR_NAME = "require_paper_sandbox"

# 예외 화이트리스트 — 이유를 명시한 상수로 관리한다. 이번 리프에서는
# 비워 둔다(레드팀 지적 결함 클래스를 새 예외로 재도입하지 않는다).
_WHITELIST: frozenset[str] = frozenset()


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
    violations: list[str] = []
    for path in sorted(_SRC_EXCHANGES.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.AsyncFunctionDef):
                continue
            if not _FUND_MOVING_NAME.match(node.name):
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
