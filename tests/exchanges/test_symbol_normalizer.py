"""BR-21(task-7868) — OKX 심볼 변환의 `symbol_normalizer`(LA-7) 위임 검증.

Spec: docs/specs/L4_execution_oms_and_exchange_v1.0.md#BR-21.

리뷰 REJECT(7802) — `okx/trading_mixin.py`가 canonical "BASE/QUOTE"를 OKX
`instId`("BASE-QUOTE")로 변환하지 않고 그대로 전송해 운영 주문이 100%
실패했다. 이 테스트는 `src/exchanges/bitget/symbols.py`(LA-19) 대칭 선례인
`tests/unit/exchanges/test_symbol_canonicalization.py`와 동일한 논리로
(1) `src/exchanges/okx/symbols.py`가 실제로 `symbol_normalizer`에 위임해
동일한 결과·동일한 예외를 내는지, (2) 왕복 변환이 항상 원래 값으로
돌아오는지, (3) OKX raw 형식("BTC-USDT")을 canonical 자리에 잘못 넣거나
그 반대일 때 fail-closed 하는지, (4) 위임을 되돌리면(회귀) 테스트가
green에서 red로 뒤집히는지(게이트적색 재현)를 확인한다.
"""

from __future__ import annotations

import importlib
import os
import subprocess
import sys
from pathlib import Path

import pytest

from src.exchanges.okx.symbols import from_okx_symbol, to_okx_symbol
from src.foundation.market_data.contracts.v1 import Venue
from src.foundation.market_data.domain.reference.symbol_normalizer import (
    SymbolNormalizationError,
    to_canonical,
    to_venue,
)

# ---------- okx/symbols.py는 symbol_normalizer(LA-7)에 위임한다 ----------


def test_to_okx_symbol_matches_normalizer() -> None:
    assert to_okx_symbol("BTC/USDT") == to_venue(Venue.OKX, "BTC/USDT") == "BTC-USDT"


def test_from_okx_symbol_matches_normalizer() -> None:
    assert from_okx_symbol("BTC-USDT") == to_canonical(Venue.OKX, "BTC-USDT") == "BTC/USDT"


def test_okx_symbol_roundtrip() -> None:
    assert from_okx_symbol(to_okx_symbol("ETH/USDT")) == "ETH/USDT"
    assert to_okx_symbol(from_okx_symbol("ETH-USDC")) == "ETH-USDC"


# ---------- 부정 테스트: 잘못된 구분자/미지 quote는 거부 ----------


def test_to_okx_symbol_rejects_dash_input_wrong_separator() -> None:
    """canonical 자리에 이미 OKX raw 형식("BTC-USDT", "/" 없음)을 넣으면
    정규화 대신 거부한다 — 옛 계약("BTC-USDT" 직접 입력)을 거부로 확정."""
    with pytest.raises(SymbolNormalizationError):
        to_okx_symbol("BTC-USDT")


def test_from_okx_symbol_rejects_slash_input_wrong_separator() -> None:
    with pytest.raises(SymbolNormalizationError):
        from_okx_symbol("BTC/USDT")


def test_to_okx_symbol_unknown_quote_raises() -> None:
    with pytest.raises(SymbolNormalizationError):
        to_okx_symbol("BTC/XYZ")


def test_from_okx_symbol_unknown_quote_raises() -> None:
    with pytest.raises(SymbolNormalizationError):
        from_okx_symbol("BTC-XYZ")


def test_from_okx_symbol_empty_base_raises() -> None:
    """raw가 quote 문자열과 정확히 같아 base가 비면(예: "USDT", "-" 없음)
    빈 base로 canonical을 조용히 만들지 않고 거부한다."""
    with pytest.raises(SymbolNormalizationError):
        from_okx_symbol("USDT")


# ---------- 게이트/CI 적색선 재현 — 위임을 되돌리면 fail-closed 테스트가 뒤집히는가 ----------
#
# LA-19/BR-21이 막으려던 바로 그 회귀 — 어댑터가 symbol_normalizer 위임을
# 버리고 검증 없는 변환을 자체 재구현 — 를 자식 pytest 프로세스에서만 소스
# 문자열 치환으로 주입한다(프로덕션 소스는 그대로).
# 선례: tests/unit/exchanges/test_symbol_canonicalization.py.

_THIS_TESTFILE = "tests/exchanges/test_symbol_normalizer.py"

_OKX_DELEGATION_GUARD = "    return _to_venue(Venue.OKX, canonical_symbol)\n"
_OKX_DELEGATION_MUTATED = '    return canonical_symbol.replace("/", "-")\n'


def _mutation_plugin_source(module_name: str, guard: str, mutated: str) -> str:
    return f"""\
import importlib
from pathlib import Path


def pytest_configure(config):
    module = importlib.import_module({module_name!r})
    source = Path(module.__file__).read_text(encoding="utf-8")
    guard = {guard!r}
    assert source.count(guard) == 1
    mutated_src = source.replace(guard, {mutated!r})
    mutant = compile(mutated_src, module.__file__, "exec")
    exec(mutant, module.__dict__)
"""


def _run_pytest_node(
    target_test: str, *, plugin_name: str | None = None, plugin_dir: Path | None = None
) -> subprocess.CompletedProcess[str]:
    repo_root = str(Path.cwd())
    command = [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", target_test]
    env = dict(os.environ, PYTHONPATH=repo_root, PYTEST_ADDOPTS="", PYTHONIOENCODING="utf-8")
    if plugin_name is not None:
        assert plugin_dir is not None
        command = [*command[:-1], "-p", plugin_name, command[-1]]
        env["PYTHONPATH"] = f"{repo_root}{os.pathsep}{plugin_dir}"
    return subprocess.run(
        command,
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        env=env,
        timeout=120,
        check=False,
    )


def test_pytest_gate_turns_red_when_okx_symbol_delegation_is_bypassed(tmp_path: Path) -> None:
    """미지 quote를 거부하는 `to_okx_symbol`이 `symbol_normalizer` 위임 대신
    검증 없는 자체 문자열 치환("/" -> "-")으로 되돌아가면(BR-21 이전 상태와
    동형인 회귀 -- 리뷰 7802가 지적한 결함의 근본 원인), 미지 quote 거부
    테스트가 green(1 passed)에서 red(1 failed)로 뒤집혀야 한다."""
    module = importlib.import_module("src.exchanges.okx.symbols")
    assert Path(module.__file__).read_text(encoding="utf-8").count(_OKX_DELEGATION_GUARD) == 1

    target_test = f"{_THIS_TESTFILE}::test_to_okx_symbol_unknown_quote_raises"
    baseline = _run_pytest_node(target_test)
    assert baseline.returncode == 0, baseline.stdout + baseline.stderr
    assert "1 passed" in baseline.stdout

    plugin_name = "_mutate_okx_symbol_delegation"
    plugin_path = tmp_path / f"{plugin_name}.py"
    plugin_path.write_text(
        _mutation_plugin_source(
            "src.exchanges.okx.symbols", _OKX_DELEGATION_GUARD, _OKX_DELEGATION_MUTATED
        ),
        encoding="utf-8",
    )

    mutated = _run_pytest_node(target_test, plugin_name=plugin_name, plugin_dir=tmp_path)
    assert mutated.returncode != 0, mutated.stdout + mutated.stderr
    assert "1 passed" not in mutated.stdout
    assert "1 failed" in mutated.stdout
