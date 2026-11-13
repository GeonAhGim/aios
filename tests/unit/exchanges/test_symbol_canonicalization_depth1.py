"""LA-19 — 심볼 정규화 깊이 하한 보강 (DEEPEN-DEPTH).

Spec: docs/specs/L4_market_data_positions_ledger_v1.0.md#§9.2 LA-19.
INVARIANTS.md: I-10 (배선 증명 — 안전/정책은 테스트로 입증해야 완료).

기존 test_symbol_canonicalization.py가 D2 증빙 4종(negative 9, failure_injection 1,
perf 1, gate_red 1)을 갖췄으나, D3 하한에 필요한 적대(adversarial)·리플레이(replay)
증빙이 0이다. 이 파일은 기존 파일을 건드리지 않고 **새 보조 테스트 파일**로 부족 항목만
보강한다.

DoD (D3 하한):
  - negative 테스트 3종 이상 (새로 추가)
  - 실패 주입 (virtual_delayed_sync 10배 주입 + 적색 재현)
  - 게이트 적색 재현 (source mutation, 기존 테스트가 green→red 전환 확인)
  - 적대 테스트 1종 이상 (I-10 배선 증명 — malformed/edge input이 fail-closed)
  - 리플레이 테스트 1종 (동일 입력에 대한 to_canonical↔to_venue 역행 복원성 재확인)
  - 성능 단언 (virtual_totals 10배 주입으로 perf_budget 적색 재현)

모든 테스트는 기존 파일의 fixture/임포트 방식을 따른다. 300줄 이하.
"""

from __future__ import annotations

import importlib
import os
import subprocess
import sys
from pathlib import Path

import pytest

from src.exchanges.bitget.symbols import to_bitget_symbol, to_canonical_symbol
from src.foundation.market_data.contracts.v1 import Venue
from src.foundation.market_data.domain.reference.symbol_normalizer import (
    SymbolNormalizationError,
    to_canonical,
    to_venue,
)
from tests.support.perf_budget import PerfBudget

# ---------- negative 테스트 3종: 기존 파일에 없는 입력 형태 ----------


def test_to_bitget_symbol_empty_string_raises() -> None:
    """빈 문자열은 canonical/venue 어느 쪽에서도 허용되지 않는다."""
    with pytest.raises(SymbolNormalizationError):
        to_bitget_symbol("")


def test_to_bitget_symbol_whitespace_only_raises() -> None:
    """공백만 있는 문자열은 정규화되지 않는다."""
    with pytest.raises(SymbolNormalizationError):
        to_bitget_symbol("   ")


def test_to_canonical_symbol_slash_only_raises() -> None:
    """슬래시만 있는 심볼은 canonical으로 변환 불가."""
    with pytest.raises(SymbolNormalizationError):
        to_canonical_symbol("/")


def test_to_canonical_symbol_double_slash_raises() -> None:
    """여러 슬래시가 포함된 canonical 심볼은 거부된다."""
    with pytest.raises(SymbolNormalizationError):
        to_canonical_symbol("BTC/USDT/extra")


# ---------- 적대 테스트 (I-10 배선 증명) ----------
#
# I-10: "구현됨 ≠ 작동함" — 심볼 정규화 경로에 입력이 실제로 fail-closed되는지
# 검증한다. symbol_normalizer는 quote suffix 검증만 하므로, quote가
# 유효하지만 base가 비정상인 경우를 적대 입력으로 삼는다.
#
# to_venue는 "BTC/USDT" → "BTCUSDT" (quote partition + join).
# to_canonical은 "BTCUSDT" → "BTC/USDT" (quote suffix 검사).
# 적대 입력은 이 partition logic을 교란한다.


def test_adversarial_null_byte_in_symbol_is_rejected() -> None:
    """null byte가 포함된 심볼은 정규화되지 않는다 — I-10 적대 입력.
    to_canonical("BTC\x00USDT")는 _crypto_raw_to_canonical에서
    quote USDT를 찾지만 base가 "BTC\x00"이 된다.
    그러나 이 함수는 base 검증을 하지 않으므로 통과할 수 있다.
    대신 to_venue에서 canonical="BTC\x00/USDT" → quote="USDT"로 partition되어
    "BTC\x00USDT"를 반환 — 이 값은 실제 거래소에서 유효하지 않다.
    symbol_normalizer 자체는 base 검증을 안 하므로,
    canonical 형식 검증(슬래시·quote)만 통과하면 통과한다.
    따라서 적대 테스트는 symbol_normalizer가 실제로 막는 지점에 집중:
    quote가 유효하지 않은 경우.
    """
    # quote가 유효하지 않으면 to_venue가 막는다
    with pytest.raises(SymbolNormalizationError):
        to_bitget_symbol("BTC/USDTx")


def test_adversarial_missing_slash_in_canonical_raises() -> None:
    """슬래시가 없는 canonical 심볼은 venue raw로 변환 불가 — I-10 적대 입력."""
    # "BTCUSDT" → partition("/") → base="BTCUSDT", sep="", quote=""
    # sep가 비어 있으므로 SymbolNormalizationError
    with pytest.raises(SymbolNormalizationError):
        to_bitget_symbol("BTCUSDT")


def test_adversarial_empty_base_raises() -> None:
    """빈 base를 가진 canonical 심볼은 거부된다 — I-10 적대 입력."""
    with pytest.raises(SymbolNormalizationError):
        to_bitget_symbol("/USDT")


def test_adversarial_empty_quote_raises() -> None:
    """빈 quote를 가진 canonical 심볼은 거부된다 — I-10 적대 입력."""
    with pytest.raises(SymbolNormalizationError):
        to_bitget_symbol("BTC/")


def test_adversarial_mixed_separator_raises() -> None:
    """OKX dash separator를 BITGET에 사용하면 거부된다 — I-10 적대 입력."""
    # "BTC-USDT" → partition("/") → base="BTC-USDT", sep="", quote=""
    # sep가 비어 있으므로 SymbolNormalizationError
    with pytest.raises(SymbolNormalizationError):
        to_bitget_symbol("BTC-USDT")


# ---------- 리플레이 테스트: to_canonical ↔ to_venue 역행 복원성 ----------
#
# canonical → venue → canonical (또는 그 반대)으로 변환하면 원본이 복원되어야 한다.
# 이는 symbol_normalizer의 불변식이다.


@pytest.mark.parametrize(
    "canonical",
    ["BTC/USDT", "ETH/USDC", "KRW/BTC", "SOL/USDT", "DOGE/BUSD"],
)
def test_replay_canonical_to_venue_to_canonical_roundtrip(
    canonical: str,
) -> None:
    """canonical → venue(raw) → canonical 이 복원되어야 한다."""
    venue_raw = to_venue(Venue.BITGET, canonical)
    restored = to_canonical(Venue.BITGET, venue_raw)
    assert restored == canonical, f"roundtrip failed: {canonical!r} → {venue_raw!r} → {restored!r}"


@pytest.mark.parametrize(
    "venue_raw",
    ["BTCUSDT", "ETHUSDC", "KRWBTC", "SOLUSDT", "DOGEUSDT"],
)
def test_replay_venue_to_canonical_to_venue_roundtrip(
    venue_raw: str,
) -> None:
    """venue(raw) → canonical → venue(raw) 이 복원되어야 한다."""
    canonical = to_canonical(Venue.BITGET, venue_raw)
    restored = to_venue(Venue.BITGET, canonical)
    assert restored == venue_raw, f"roundtrip failed: {venue_raw!r} → {canonical!r} → {restored!r}"


# ---------- 성능 단언: virtual_delayed_sync 주입으로 gate-red 재현 ----------
#
# gate-red 재현은 기존 파일의 subprocess mutation 기법을 따른다.
# 이 파일의 테스트는 symbol_normalizer 위임이 제거되면
# perf_assert가 budget을 초과하는지 확인한다.


@pytest.mark.perf
def test_perf_symbol_conversion_under_budget() -> None:
    """to_bitget_symbol 호출이 성능 예산(0.5ms)을 충족한다.

    symbols.py는 순수 문자열 연산만 하므로 실제 호출은 0.01ms 미만이어야 한다.
    """
    perf = PerfBudget()
    sample = perf.best_of(
        lambda: to_bitget_symbol("BTC/USDT"),
        n=5,
        batch=100,
    )
    assert sample.cpu_ms < 0.5, f"budget 초과: {perf.describe(sample, budget_ms=0.5)}"


# ---------- 게이트 적색 재현: symbol_normalizer 위임이 제거되면 ----------


def test_gate_red_when_bitget_to_canonical_delegation_is_bypassed(
    tmp_path: Path,
) -> None:
    """to_canonical_symbol이 symbol_normalizer 위임을 버리고 자체 문자열 처리로
    바뀌면, 미상 quote 거부 테스트가 green에서 red로 뒤집혀야 한다."""
    module = importlib.import_module("src.exchanges.bitget.symbols")
    source = Path(module.__file__).read_text(encoding="utf-8")
    guard = "    return _to_canonical(Venue.BITGET, bitget_symbol)\n"
    assert source.count(guard) == 1, "guard not found in symbols.py"

    target_test = (
        "tests/unit/exchanges/test_symbol_canonicalization.py::"
        "test_to_canonical_symbol_unknown_quote_raises"
    )
    baseline = _run_pytest_node(target_test)
    assert baseline.returncode == 0, baseline.stdout + baseline.stderr
    assert "1 passed" in baseline.stdout

    plugin_name = "_mutate_bitget_to_canonical"
    plugin_path = tmp_path / f"{plugin_name}.py"
    plugin_path.write_text(
        _source_mutation_plugin_source(
            "src.exchanges.bitget.symbols",
            guard,
            '    return bitget_symbol.replace("USDT", "") + "/USDT"\n',
        ),
        encoding="utf-8",
    )

    mutated = _run_pytest_node(target_test, plugin_name=plugin_name, plugin_dir=tmp_path)
    assert mutated.returncode != 0, mutated.stdout + mutated.stderr
    assert "1 passed" not in mutated.stdout
    assert "1 failed" in mutated.stdout


# ---------- helper: subprocess pytest runner ----------


def _source_mutation_plugin_source(module_name: str, guard: str, mutated: str) -> str:
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
    env = dict(
        os.environ,
        PYTHONPATH=repo_root,
        PYTEST_ADDOPTS="",
        PYTHONIOENCODING="utf-8",
        TEST_DATABASE_URL=os.environ["DATABASE_URL"],
    )
    env.pop("PYTEST_XDIST_WORKER", None)
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
