"""L4-13(task-1519) — `build_adapter()` LIVE 차단 + paper_sim 등록 훅.

Spec: docs/specs/L4_execution_oms_and_exchange_v1.0.md §2-B(factory.py 행), §4.3, §9 L4-13
BACKLOG_devengine_v1 §1 L4-13 negative_test: "환경변수 없이 demo_mode=False
요청 시 통과하면 실패".

I-10(배선·우회불가·증명) — 가드가 factory 안에만 있고 운영 코드가 어댑터를
직접 생성하면 무의미하므로, (a) 운영 배선의 기본 `adapter_factory`가
`build_adapter`인지, (b) `src/**`에 factory 밖 직접 생성자 호출이
demo/paper 리터럴로만 존재하는지를 AST로 함께 증명한다.
"""
from __future__ import annotations

import ast
import inspect
from pathlib import Path

import pytest

from src.core.exceptions import FrozenZonePaperAdapterBlockedError
from src.exchanges import factory as factory_module
from src.exchanges.bitget.adapter import BitgetAdapter
from src.exchanges.factory import (
    LIVE_ADAPTER_ENV,
    SUPPORTED_EXCHANGES,
    PaperSimAdapterUnavailableError,
    UnsupportedExchangeError,
    build_adapter,
    live_adapter_allowed,
    register_paper_sim_factory,
    reset_paper_sim_factory,
)

_BITGET_EXTRA = {"api_passphrase": "p"}
_KIS_EXTRA = {"cano": "12345678", "acnt_prdt_cd": "01"}
_NH_EXTRA = {"act_no": "1234567890"}


@pytest.fixture(autouse=True)
def _no_live_env(monkeypatch: pytest.MonkeyPatch):
    """모든 테스트는 환경변수가 **없는** 상태에서 시작한다 — 개발자 셸에
    우연히 남은 값이 테스트를 통과시키는 거짓 초록을 막는다."""
    monkeypatch.delenv(LIVE_ADAPTER_ENV, raising=False)
    reset_paper_sim_factory()
    yield
    reset_paper_sim_factory()


# ---------------------------------------------------------------------------
# demo_mode=False 차단 (negative test)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("exchange", "extra"),
    [("bitget", _BITGET_EXTRA), ("kis", _KIS_EXTRA), ("nh", _NH_EXTRA)],
)
def test_live_adapter_blocked_without_env(exchange: str, extra: dict[str, str]):
    """negative_test(BACKLOG L4-13) — 환경변수 없이 demo_mode=False가
    통과하면 실패."""
    with pytest.raises(FrozenZonePaperAdapterBlockedError):
        build_adapter(exchange, "key", "secret", extra, demo_mode=False)


def test_live_block_precedes_exchange_validation():
    """가드가 거래소 분기보다 먼저다 — 미지 거래소/필드 누락 오류로 가드를
    건너뛰는 경로가 없다."""
    with pytest.raises(FrozenZonePaperAdapterBlockedError):
        build_adapter("unknown", "key", "secret", None, demo_mode=False)
    with pytest.raises(FrozenZonePaperAdapterBlockedError):
        build_adapter("bitget", "key", "secret", None, demo_mode=False)  # passphrase 누락
    with pytest.raises(FrozenZonePaperAdapterBlockedError):
        build_adapter("paper_sim", "key", "secret", None, demo_mode=False)


@pytest.mark.parametrize("value", ["true", "yes", "TRUE", "on", " 1", "1 ", "0", ""])
def test_live_block_rejects_loose_truthy_values(monkeypatch: pytest.MonkeyPatch, value: str):
    """negative — 정확히 "1"만 허용(fail-closed). truthy 해석은 오타 하나로
    실계정 어댑터를 여는 경로다."""
    monkeypatch.setenv(LIVE_ADAPTER_ENV, value)
    assert live_adapter_allowed() is False
    with pytest.raises(FrozenZonePaperAdapterBlockedError):
        build_adapter("bitget", "key", "secret", _BITGET_EXTRA, demo_mode=False)


def test_live_adapter_allowed_only_with_exact_env_value(monkeypatch: pytest.MonkeyPatch):
    """스펙 §2-B: `AIOS_ALLOW_LIVE_ADAPTER=1`일 때만 생성된다. 생성돼도
    `@require_paper_sandbox`/Executor 이중 확인은 그대로 남아 주문은
    여전히 차단된다(가드 해제가 아니라 생성 허용일 뿐)."""
    monkeypatch.setenv(LIVE_ADAPTER_ENV, "1")
    assert live_adapter_allowed() is True
    adapter = build_adapter("bitget", "key", "secret", _BITGET_EXTRA, demo_mode=False)
    assert isinstance(adapter, BitgetAdapter)
    assert adapter.is_paper_trading is False
    assert adapter.is_sandboxed is False


def test_demo_mode_default_true_unaffected():
    """기존 호출(demo_mode 생략)은 환경변수와 무관하게 그대로 동작한다."""
    adapter = build_adapter("bitget", "key", "secret", _BITGET_EXTRA)
    assert adapter.is_paper_trading is True
    assert adapter.is_sandboxed is True


async def test_live_adapter_created_with_env_still_cannot_place_order(
    monkeypatch: pytest.MonkeyPatch,
):
    """세 방어선의 독립성 — factory 가드를 환경변수로 열어도 메서드 레벨
    가드(task-1045/1356)는 그대로 실주문을 막는다(ADR-2026-08-29-E:
    완화 금지)."""
    monkeypatch.setenv(LIVE_ADAPTER_ENV, "1")
    adapter = build_adapter("bitget", "key", "secret", _BITGET_EXTRA, demo_mode=False)
    with pytest.raises(FrozenZonePaperAdapterBlockedError):
        await adapter.cancel_order("1")


# ---------------------------------------------------------------------------
# paper_sim 등록 훅
# ---------------------------------------------------------------------------


def test_paper_sim_registered_in_supported_exchanges():
    assert "paper_sim" in SUPPORTED_EXCHANGES
    assert "nh" in SUPPORTED_EXCHANGES


def test_paper_sim_without_registered_factory_raises_explicitly():
    """negative — 훅 미등록 시 다른 어댑터로 무음 폴백하지 않는다."""
    with pytest.raises(PaperSimAdapterUnavailableError) as info:
        build_adapter("paper_sim", "key", "secret", None)
    assert isinstance(info.value, UnsupportedExchangeError)


def test_paper_sim_uses_registered_factory():
    sentinel = object()
    calls: list[tuple[str, str, dict[str, str]]] = []

    def _factory(api_key: str, api_secret: str, extra: dict[str, str]):
        calls.append((api_key, api_secret, extra))
        return sentinel

    register_paper_sim_factory(_factory)  # type: ignore[arg-type]
    result = build_adapter("paper_sim", "k", "s", {"reference": "bitget"})

    assert result is sentinel
    assert calls == [("k", "s", {"reference": "bitget"})]


def test_reset_paper_sim_factory_restores_explicit_failure():
    register_paper_sim_factory(lambda k, s, e: object())  # type: ignore[arg-type, return-value]
    reset_paper_sim_factory()
    with pytest.raises(PaperSimAdapterUnavailableError):
        build_adapter("paper_sim", "k", "s", None)


# ---------------------------------------------------------------------------
# I-10 배선 증명 — factory가 운영 경로의 유일한 생성 지점
# ---------------------------------------------------------------------------

_SRC = Path(factory_module.__file__).resolve().parents[2] / "src"
_ADAPTER_CTORS = {"BitgetAdapter", "KISAdapter", "NHAdapter"}
_MODE_KWARGS = {"demo_mode", "is_paper_trading"}


def _direct_ctor_calls_outside_factory() -> list[str]:
    """`src/**`에서 factory.py 밖의 어댑터 직접 생성 중, 모드 인자를
    `True` 리터럴이 아닌 값으로 넘기는 호출(= 가드를 우회해 실계정
    어댑터를 만들 수 있는 경로)을 나열한다. 모드 인자 생략은 생성자
    기본값(True)이므로 허용."""
    offenders: list[str] = []
    for path in sorted(_SRC.rglob("*.py")):
        if "__pycache__" in path.parts or path.name == "factory.py":
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            name = func.id if isinstance(func, ast.Name) else (
                func.attr if isinstance(func, ast.Attribute) else None
            )
            if name not in _ADAPTER_CTORS:
                continue
            for kw in node.keywords:
                if kw.arg in _MODE_KWARGS and not (
                    isinstance(kw.value, ast.Constant) and kw.value.value is True
                ):
                    offenders.append(f"{path.relative_to(_SRC.parent)}:{node.lineno}:{name}")
    return offenders


def test_no_direct_live_adapter_construction_outside_factory():
    offenders = _direct_ctor_calls_outside_factory()
    assert not offenders, (
        "factory 가드를 우회하는 어댑터 직접 생성(모드 인자가 True 리터럴이 아님):\n"
        + "\n".join(offenders)
    )


def test_wiring_scanner_detects_non_literal_mode():
    """스캐너 자체의 회귀 방지(거짓 초록 방지)."""
    src = "def f(flag):\n    return BitgetAdapter('k', 's', 'p', demo_mode=flag)\n"
    tree = ast.parse(src)
    call = next(n for n in ast.walk(tree) if isinstance(n, ast.Call))
    kw = call.keywords[0]
    assert kw.arg == "demo_mode"
    assert not (isinstance(kw.value, ast.Constant) and kw.value.value is True)


def test_operational_wiring_defaults_to_guarded_factory():
    """credential_resolver/exchange_credential_service의 기본 adapter_factory가
    `build_adapter`다 — 가드가 실제 운영 경로에 배선돼 있다는 증명."""
    from src.services.credential_resolver import CredentialResolver
    from src.services.exchange_credential_service import ExchangeCredentialService

    for cls in (CredentialResolver, ExchangeCredentialService):
        param = inspect.signature(cls.__init__).parameters["adapter_factory"]
        assert param.default is build_adapter, cls.__name__
