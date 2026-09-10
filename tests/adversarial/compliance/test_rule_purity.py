"""CM-21 적대적 — CM-A2: 컴플라이언스 규칙 평가는 네트워크·LLM 호출을 포함하지
않는다.

Spec: docs/specs/L4_compliance_and_regulatory_v1.0.md §9 CM-21, §9 CM-A2
("규칙 평가는 순수·결정론이며 LLM·네트워크 호출을 포함하지 않는다(적대적 테스트로
증명, R-56과 동형)"), ADR-2026-09-06-G §8.

CM-A2는 지금까지 선언만 하고 검사하지 않았다 — `domain/rules/*.py`/
`domain/evaluator.py`의 각 모듈 docstring이 "no I/O"를 주장할 뿐, 그 주장이
깨졌을 때 실패하는 테스트가 없었다. 여기서는 두 층으로 그 주장을 강제한다:

1. 정적 — `domain/rules/**` + `domain/evaluator.py` + `domain/rule_bundle.py`에서
   출발해 `src.*` import를 추이적으로 따라가며 `socket`/`httpx`/`anthropic`/
   `openai`(및 서브모듈, 동적 `__import__`/`importlib.import_module` 문자열
   리터럴)에 닿는 경로가 0건임을 단언한다(R-56 `test_no_llm_in_risk_path.py`와
   동형). anthropic/openai는 이 venv에 설치돼 있지 않지만 AST 파싱은 설치
   여부와 무관하다.
2. 런타임 — `socket.socket`/`socket.create_connection`/`httpx.Client.__init__`/
   `httpx.AsyncClient.__init__`을 몽키패치해 호출 시 예외를 던지고 호출을
   기록하게 만든 다음, 7개 규칙 전부(`check(params, snapshot)`) + `domain/
   rules/__init__.py`의 정책 함수 + `evaluator.evaluate_bundle`을 실제
   입력으로 실행해 기록된 호출이 0건임을 확인한다. `anthropic`/`openai`는
   설치돼 있지 않으므로 접근 시 기록·예외를 던지는 가짜 모듈을 `sys.modules`에
   주입해 몽키패치를 흉내낸다.

`evaluator.evaluate_bundle`은 규칙이 던진 예외를 fail-closed DENY로 삼켜버리므로
(`_run_rule`), 런타임 계층은 예외 전파에만 의존하지 않고 **호출 기록 리스트**로도
위반을 잡는다 — 그래야 "evaluator가 예외를 삼켜서 위반이 조용히 DENY 뒤에
숨는" 시나리오도 놓치지 않는다. 마지막으로, 실제 규칙 모듈 하나
(`restricted_list.check`)에 의도적으로 `httpx` 호출을 주입해 이 테스트가
반드시 실패(예외 전파 또는 기록된 호출 비어있지 않음)함을 증명한다(DoD).
"""
from __future__ import annotations

import ast
import socket
import sys
import types
from collections import deque
from collections.abc import Callable, Mapping
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any
from uuid import UUID

import httpx
import pytest

from src.foundation.mandates.contracts.v1 import ComplianceVerdict, RuleHit
from src.foundation.mandates.domain import rules as rules_policy
from src.foundation.mandates.domain.evaluator import evaluate_bundle
from src.foundation.mandates.domain.models import (
    Autonomy,
    MandateRevision,
    MandateRevisionState,
    PolicyEvaluationSubject,
)
from src.foundation.mandates.domain.rule_bundle import RuleBundle, RuleSpec
from src.foundation.mandates.domain.rules import (
    concentration,
    leverage,
    liquidity,
    position_limit,
    restricted_list,
    short_sale,
    wash_trade,
)

ROOT = Path(__file__).resolve().parents[3]

GUARDED_PATHS: tuple[str, ...] = (
    "src/foundation/mandates/domain/rules",
    "src/foundation/mandates/domain/evaluator.py",
    "src/foundation/mandates/domain/rule_bundle.py",
)

BANNED_MODULES: frozenset[str] = frozenset({"socket", "httpx", "anthropic", "openai"})
_DYNAMIC_IMPORT_CALLS = frozenset({"__import__", "import_module"})

Violation = tuple[str, str, tuple[str, ...]]  # (file, banned module, import chain)

_NOW = datetime(2026, 9, 10, 0, 0, tzinfo=timezone.utc)


# --- 1. 정적 추이적 import 그래프 (R-56과 동형) ------------------------------


def matches_banned(module: str, banned: frozenset[str]) -> str | None:
    for name in banned:
        if module == name or module.startswith(name + "."):
            return name
    return None


def _imported_names(tree: ast.AST) -> list[str]:
    names: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            names.append(node.module)
            names.extend(f"{node.module}.{alias.name}" for alias in node.names)
        elif isinstance(node, ast.Call):
            func = node.func
            callee = func.id if isinstance(func, ast.Name) else getattr(func, "attr", None)
            if callee in _DYNAMIC_IMPORT_CALLS and node.args:
                first = node.args[0]
                if isinstance(first, ast.Constant) and isinstance(first.value, str):
                    names.append(first.value)
    return names


def module_to_path(module: str, root: Path) -> Path | None:
    parts = module.split(".")
    while parts:
        candidate = root.joinpath(*parts)
        if candidate.is_dir() and (candidate / "__init__.py").exists():
            return candidate / "__init__.py"
        if candidate.with_suffix(".py").exists():
            return candidate.with_suffix(".py")
        parts.pop()
    return None


def _expand(root: Path, guarded: tuple[str, ...]) -> list[Path]:
    files: list[Path] = []
    for rel in guarded:
        path = root / rel
        files.extend([path] if path.is_file() else sorted(path.rglob("*.py")))
    return files


def scan_import_graph(
    root: Path, guarded: tuple[str, ...], banned: frozenset[str]
) -> tuple[list[Violation], set[Path]]:
    start = _expand(root, guarded)
    parent: dict[Path, Path | None] = {path: None for path in start}
    queue = deque(start)
    violations: list[Violation] = []

    def chain(path: Path) -> tuple[str, ...]:
        links: list[str] = []
        cursor: Path | None = path
        while cursor is not None:
            links.append(cursor.relative_to(root).as_posix())
            cursor = parent[cursor]
        return tuple(reversed(links))

    while queue:
        path = queue.popleft()
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for module in _imported_names(tree):
            hit = matches_banned(module, banned)
            if hit is not None:
                violations.append((path.relative_to(root).as_posix(), module, chain(path)))
                continue
            if not module.startswith("src."):
                continue
            target = module_to_path(module, root)
            if target is not None and target not in parent:
                parent[target] = path
                queue.append(target)
    return violations, set(parent)


def _format(violations: list[Violation]) -> str:
    return "\n".join(
        f"{file} imports {module} via {' -> '.join(chain)}" for file, module, chain in violations
    )


def test_guarded_paths_exist() -> None:
    missing = [rel for rel in GUARDED_PATHS if not (ROOT / rel).exists()]
    assert not missing, f"CM-A2 보호 경로가 없음(스펙 §9 갱신 필요): {missing}"


def test_cm_a2_rules_reach_no_network_or_llm_module_transitively() -> None:
    violations, reached = scan_import_graph(ROOT, GUARDED_PATHS, BANNED_MODULES)
    assert not violations, "CM-A2 위반 — 규칙 경로에서 네트워크/LLM 모듈 도달:\n" + _format(
        violations
    )
    assert len(reached) >= len(_expand(ROOT, GUARDED_PATHS))
    assert ROOT / "src/foundation/mandates/domain/evaluator.py" in reached
    assert ROOT / "src/foundation/mandates/domain/rules/restricted_list.py" in reached


_CLEAN_ROOT = "from src.services.helper import compute\n"
_INJECTIONS: dict[str, str] = {
    "direct_socket_import": "import socket\n",
    "from_httpx_import": "from httpx import Client\n",
    "anthropic_import": "import anthropic\n",
    "openai_from_import": "from openai import OpenAI\n",
    "dynamic_import_module": "import importlib\nclient = importlib.import_module('httpx')\n",
    "dynamic_dunder_import": "mod = __import__('anthropic')\n",
    "function_local_import": "def call():\n    import socket\n    return socket\n",
}


def _fake_tree(tmp_path: Path, helper_source: str) -> Path:
    (tmp_path / "src/foundation/mandates/domain/rules").mkdir(parents=True)
    (tmp_path / "src/services").mkdir(parents=True)
    packages = (
        "src",
        "src/foundation",
        "src/foundation/mandates",
        "src/foundation/mandates/domain",
        "src/foundation/mandates/domain/rules",
        "src/services",
    )
    for pkg in packages:
        (tmp_path / pkg / "__init__.py").write_text("", encoding="utf-8")
    (tmp_path / "src/foundation/mandates/domain/rules/restricted_list.py").write_text(
        _CLEAN_ROOT, encoding="utf-8"
    )
    (tmp_path / "src/services/helper.py").write_text(
        helper_source + "\ndef compute():\n    return 1\n", encoding="utf-8"
    )
    return tmp_path


@pytest.mark.parametrize("injection", sorted(_INJECTIONS))
def test_negative_injected_io_import_is_detected_through_intermediate_module(
    tmp_path: Path, injection: str
) -> None:
    root = _fake_tree(tmp_path, _INJECTIONS[injection])
    violations, _ = scan_import_graph(
        root, ("src/foundation/mandates/domain/rules",), BANNED_MODULES
    )
    assert violations, f"체커 결함 — 위조 주입({injection})을 놓침"
    file, _module, chain = violations[0]
    assert file == "src/services/helper.py"
    assert chain == (
        "src/foundation/mandates/domain/rules/restricted_list.py",
        "src/services/helper.py",
    )


def test_negative_control_clean_tree_has_no_violation(tmp_path: Path) -> None:
    root = _fake_tree(tmp_path, "import decimal\n")
    violations, reached = scan_import_graph(
        root, ("src/foundation/mandates/domain/rules",), BANNED_MODULES
    )
    assert violations == []
    assert root / "src/services/helper.py" in reached


# --- 2. 런타임 몽키패치 증명 -------------------------------------------------


class NetworkAccessError(RuntimeError):
    """CM-A2 위반 — 규칙 평가 중 금지된 I/O 원시 함수가 호출됐다."""


class _PoisonModule(types.ModuleType):
    """`anthropic`/`openai`가 설치되지 않은 venv에서도 "import 시도"를 잡기
    위한 가짜 모듈. 어떤 속성 접근이든(클라이언트 클래스 등) 호출을 기록하고
    예외를 던진다."""

    def __init__(self, name: str, calls: list[str]) -> None:
        super().__init__(name)
        object.__setattr__(self, "_calls", calls)

    def __getattr__(self, attr: str) -> Any:
        self._calls.append(f"{self.__name__}.{attr}")
        raise NetworkAccessError(
            f"CM-A2 violation: {self.__name__}.{attr} accessed during rule evaluation"
        )


def _install_io_traps(monkeypatch: pytest.MonkeyPatch, calls: list[str]) -> None:
    def _trap(name: str) -> Callable[..., Any]:
        def _raise(*_args: Any, **_kwargs: Any) -> Any:
            calls.append(name)
            raise NetworkAccessError(
                f"CM-A2 violation: {name} invoked during rule evaluation"
            )

        return _raise

    monkeypatch.setattr(socket, "socket", _trap("socket.socket"))
    monkeypatch.setattr(socket, "create_connection", _trap("socket.create_connection"))
    monkeypatch.setattr(httpx.Client, "__init__", _trap("httpx.Client.__init__"))
    monkeypatch.setattr(httpx.AsyncClient, "__init__", _trap("httpx.AsyncClient.__init__"))
    monkeypatch.setattr(httpx, "request", _trap("httpx.request"))
    monkeypatch.setattr(httpx, "get", _trap("httpx.get"))
    monkeypatch.setattr(httpx, "post", _trap("httpx.post"))

    for module_name in ("anthropic", "openai"):
        monkeypatch.setitem(sys.modules, module_name, _PoisonModule(module_name, calls))


# --- 규칙별 대표 (params, snapshot) — hit/no-hit/missing-field 분기를 고루 실행 ---

_RESTRICTED_LIST_CASES: list[tuple[Mapping[str, Any], Mapping[str, Any]]] = [
    ({"restricted_symbols": ("AAPL",)}, {"symbol": "AAPL"}),
    ({"restricted_symbols": ("AAPL",)}, {"symbol": "MSFT"}),
    ({}, {}),
]

_CONCENTRATION_CASES: list[tuple[Mapping[str, Any], Mapping[str, Any]]] = [
    ({"max_single_instrument_pct": 10.0}, {"projected_instrument_pct": 15.0}),
    ({"max_single_instrument_pct": 10.0}, {"projected_instrument_pct": 5.0}),
    ({}, {}),
]

_LEVERAGE_CASES: list[tuple[Mapping[str, Any], Mapping[str, Any]]] = [
    ({"max_leverage": Decimal("2.0")}, {"projected_gross_leverage": Decimal("3.0")}),
    ({"max_leverage": Decimal("2.0")}, {"projected_gross_leverage": Decimal("1.0")}),
    ({}, {}),
]

_LIQUIDITY_CASES: list[tuple[Mapping[str, Any], Mapping[str, Any]]] = [
    (
        {"max_pct_of_adv": Decimal("5")},
        {"order_notional": Decimal("1000"), "average_daily_traded_value": Decimal("10000")},
    ),
    (
        {"max_pct_of_adv": Decimal("50")},
        {"order_notional": Decimal("1000"), "average_daily_traded_value": Decimal("10000")},
    ),
    (
        {"max_pct_of_adv": Decimal("5")},
        {"order_notional": Decimal("1000"), "average_daily_traded_value": Decimal("0")},
    ),
    ({}, {}),
]

_POSITION_LIMIT_CASES: list[tuple[Mapping[str, Any], Mapping[str, Any]]] = [
    (
        {"max_position_notional": Decimal("100000")},
        {"projected_position_notional": Decimal("150000")},
    ),
    (
        {"max_position_notional": Decimal("100000")},
        {"projected_position_notional": Decimal("50000")},
    ),
    ({}, {}),
]

_SHORT_SALE_CASES: list[tuple[Mapping[str, Any], Mapping[str, Any]]] = [
    (
        {},
        {
            "side": "SELL",
            "order_qty": Decimal("100"),
            "position_qty": Decimal("0"),
            "borrow_available_qty": Decimal("50"),
        },
    ),
    (
        {},
        {
            "side": "SELL",
            "order_qty": Decimal("100"),
            "position_qty": Decimal("0"),
            "borrow_available_qty": Decimal("100"),
        },
    ),
    ({}, {"side": "BUY"}),
    (
        {"krx_uptick_required": True},
        {
            "side": "SELL",
            "order_qty": Decimal("10"),
            "position_qty": Decimal("0"),
            "borrow_available_qty": Decimal("10"),
            "venue": "KRX",
            "order_price": Decimal("99"),
            "last_price": Decimal("100"),
        },
    ),
    ({}, {}),
]

_WASH_TRADE_CASES: list[tuple[Mapping[str, Any], Mapping[str, Any]]] = [
    (
        {},
        {
            "tenant_id": "t1",
            "instrument": "AAPL",
            "side": "BUY",
            "order_price": Decimal("100"),
            "open_orders": [
                {"tenant_id": "t1", "instrument": "AAPL", "side": "SELL", "price": Decimal("90")}
            ],
        },
    ),
    (
        {},
        {
            "tenant_id": "t1",
            "instrument": "AAPL",
            "side": "BUY",
            "order_price": Decimal("100"),
            "open_orders": [],
        },
    ),
    ({}, {}),
]

_RuleCaseList = list[tuple[Mapping[str, Any], Mapping[str, Any]]]
_ALL_RULE_MODULES: tuple[tuple[str, Any, _RuleCaseList], ...] = (
    ("restricted_list", restricted_list, _RESTRICTED_LIST_CASES),
    ("concentration", concentration, _CONCENTRATION_CASES),
    ("leverage", leverage, _LEVERAGE_CASES),
    ("liquidity", liquidity, _LIQUIDITY_CASES),
    ("position_limit", position_limit, _POSITION_LIMIT_CASES),
    ("short_sale", short_sale, _SHORT_SALE_CASES),
    ("wash_trade", wash_trade, _WASH_TRADE_CASES),
)


def _sample_revision() -> MandateRevision:
    return MandateRevision(
        id=UUID(int=1),
        mandate_id=UUID(int=2),
        revision_no=1,
        state=MandateRevisionState.ACTIVE,
        max_total_exposure_pct=80.0,
        max_single_instrument_pct=10.0,
        min_cash_buffer_pct=5.0,
        max_daily_loss_pct=3.0,
        allowed_autonomy=Autonomy.PAPER,
        forbidden_assets=("XYZ",),
    )


def _sample_subject() -> PolicyEvaluationSubject:
    return PolicyEvaluationSubject(
        command_type="ORDER_SUBMIT",
        instrument_exposure_pct=15.0,
        total_exposure_pct=90.0,
        cash_buffer_pct=2.0,
        projected_daily_loss_pct=5.0,
        requested_autonomy=Autonomy.LIMITED_LIVE,
        asset="XYZ",
    )


def _run_all_domain_rules_and_evaluator() -> None:
    """`domain/rules/*` 전 규칙 + `domain/rules/__init__.py` 정책 함수 +
    `domain/evaluator.py`를 실제 입력으로 실행한다. I/O 트랩이 걸린 상태에서
    호출하면 위반 즉시 `NetworkAccessError`가 전파된다."""
    for _rule_id, module, cases in _ALL_RULE_MODULES:
        for params, snapshot in cases:
            module.check(params, snapshot)

    revision = _sample_revision()
    proposed = _sample_revision()
    subject = _sample_subject()
    rules_policy.compute_revision_hash(revision)
    rules_policy.compile_rule_hash(revision)
    rules_policy.compiler_version()
    rules_policy.detect_material_change(revision, proposed)
    rules_policy.evaluate_policy(revision, subject)

    # evaluate_bundle: 공유 스냅샷 하나로 7개 규칙 전부 ALLOW하는 해피 패스
    # (evaluator 자신의 정렬/해시/판정 합성 코드도 I/O 0회여야 한다).
    snapshot = {
        "symbol": "MSFT",
        "projected_instrument_pct": 5.0,
        "projected_gross_leverage": Decimal("1.0"),
        "order_notional": Decimal("1000"),
        "average_daily_traded_value": Decimal("100000"),
        "projected_position_notional": Decimal("1000"),
        "side": "BUY",
        "order_qty": Decimal("10"),
        "position_qty": Decimal("10"),
        "borrow_available_qty": Decimal("0"),
        "venue": "NASDAQ",
        "order_price": Decimal("100"),
        "last_price": Decimal("100"),
        "tenant_id": "t1",
        "instrument": "MSFT",
        "open_orders": [],
    }
    bundle = RuleBundle(
        version="purity-test-v1",
        rules=(
            RuleSpec(
                rule_id="restricted_list",
                params={"restricted_symbols": ("AAPL",)},
                check=restricted_list.check,
            ),
            RuleSpec(
                rule_id="concentration",
                params={"max_single_instrument_pct": 10.0},
                check=concentration.check,
            ),
            RuleSpec(
                rule_id="leverage",
                params={"max_leverage": Decimal("2.0")},
                check=leverage.check,
            ),
            RuleSpec(
                rule_id="liquidity",
                params={"max_pct_of_adv": Decimal("50")},
                check=liquidity.check,
            ),
            RuleSpec(
                rule_id="position_limit",
                params={"max_position_notional": Decimal("100000")},
                check=position_limit.check,
            ),
            RuleSpec(rule_id="short_sale", params={}, check=short_sale.check),
            RuleSpec(rule_id="wash_trade", params={}, check=wash_trade.check),
        ),
    )
    decision = evaluate_bundle(bundle, snapshot, now=_NOW)
    assert decision.verdict == ComplianceVerdict.ALLOW
    assert decision.rule_hits == []


def test_domain_rules_and_evaluator_perform_zero_io(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []
    _install_io_traps(monkeypatch, calls)

    _run_all_domain_rules_and_evaluator()

    assert calls == [], f"CM-A2 위반 — 규칙 평가 중 I/O 호출 발생: {calls}"


# --- 3. 부정 증명: 규칙 하나에 httpx 호출을 주입하면 이 테스트가 실패한다 -------


def test_negative_injecting_httpx_call_into_one_rule_makes_purity_check_fail(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """DoD: "규칙 하나에 의도적 httpx 호출을 넣으면 테스트가 실패함을 증명."
    실제 `restricted_list.check`(CM-6 규칙 모듈)를 몽키패치해 원래 로직을
    그대로 수행하되 그 전에 `httpx.get`을 호출하도록 만든다. 직접 호출
    경로(위 §2와 동일한 스윕)에서는 그 호출 즉시 `NetworkAccessError`가
    전파돼야 한다 — 이 테스트가 실패로 재현되지 않으면 순수성 검사 자체가
    무의미하다."""
    calls: list[str] = []
    _install_io_traps(monkeypatch, calls)

    original_check = restricted_list.check

    def _poisoned_check(params: Mapping[str, Any], snapshot: Mapping[str, Any]) -> RuleHit | None:
        httpx.get("http://example.invalid/exfiltrate")  # 의도적 위반
        return original_check(params, snapshot)

    monkeypatch.setattr(restricted_list, "check", _poisoned_check)

    with pytest.raises(NetworkAccessError):
        _run_all_domain_rules_and_evaluator()

    assert "httpx.Client.__init__" in calls or "httpx.get" in calls


def test_negative_evaluator_fail_closed_swallow_does_not_hide_io_from_the_trap(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`evaluator._run_rule`은 규칙이 던진 예외를 삼켜 DENY로 바꾼다(fail-closed).
    이 삼킴이 CM-A2 위반 탐지까지 가려서는 안 된다 — 호출 기록 리스트는
    evaluator를 거쳐도 여전히 위반을 담고 있어야 한다."""
    calls: list[str] = []
    _install_io_traps(monkeypatch, calls)

    def _poison_rule(_params: Mapping[str, Any], _snapshot: Mapping[str, Any]) -> RuleHit | None:
        httpx.get("http://example.invalid/exfiltrate")
        return None

    bundle = RuleBundle(
        version="poison-v1", rules=(RuleSpec(rule_id="POISON", params={}, check=_poison_rule),)
    )

    # evaluate_bundle自身은 예외를 삼켜 DENY로 반환한다 — 조용히 통과하지 않는다.
    decision = evaluate_bundle(bundle, {}, now=_NOW)
    assert decision.verdict == ComplianceVerdict.DENY

    # 하지만 "규칙 평가는 I/O 0회"라는 순수성 단언은 여전히 깨져야 한다.
    with pytest.raises(AssertionError):
        assert calls == [], f"CM-A2 violation would have gone undetected: {calls}"
    assert calls
