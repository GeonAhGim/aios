"""LA-19 — 거래소 심볼 변환의 `symbol_normalizer`(LA-7) 위임 검증.

Spec: docs/specs/L4_market_data_positions_ledger_v1.0.md#§9.2 LA-19.

FULL_AUDIT_2026-09-02.md §7 — 어댑터 자체 변환과 LA-7 단일 규칙이 따로 놀면
주문 시 "BTC/USDT", 조회 시 "BTCUSDT"처럼 조용히 어긋날 수 있다. 이 테스트는
(1) `src/exchanges/bitget/symbols.py`가 실제로 `symbol_normalizer`에 위임해
동일한 결과·동일한 예외를 내는지, (2) KIS market data 메서드가 형식이
잘못된 심볼을 조용히 거래소로 흘려보내지 않고 fail-closed 하는지 확인한다.
실거래소 호출은 없음 — httpx.MockTransport만 사용.

DEEPEN(task-2963, DEPTH_LA_LB_LC.md #488, 원 커밋 0edadcb 실측 D1) — 위 내용은
D1(negative 5건)까지만 갖췄었다. 이 파일 뒤쪽에 D3까지 올리는 3축을 추가한다:
(1) 실패주입 — 입력검증(심볼 형식)과 무관하게, 유효한 심볼인데 거래소 응답
자체가 손상된 경우(실 어댑터 결함) fail-closed 하는지. KIS get_ohlcv 계열은
get_ticker/get_orderbook과 달리 응답 파싱을 try/except로 감싸지 않아 bare
KeyError가 새어나갔다 — 이 리프에서 함께 고쳤다(market_data_mixin.py).
(2) 수치 성능 단언 — symbols.py는 순수 함수(I/O 없음)라 트리비얼 문자열
연산 베이스라인 대비 정규화 배율로 처리량 회귀를 잡는다.
(3) 게이트 적색 재현 — LA-19가 막으려던 바로 그 회귀(어댑터가
symbol_normalizer 위임을 버리고 자체 검증 없는 변환을 재구현)를 자식 pytest
프로세스에서만 주입해, 기존 fail-closed 테스트가 green에서 red로
뒤집힘을 실측한다.
"""

from __future__ import annotations

import importlib
import os
import subprocess
import sys
import time
from pathlib import Path

import httpx
import pytest

from src.core.exceptions import FatalExchangeError
from src.exchanges.bitget.adapter import BitgetAdapter
from src.exchanges.bitget.symbols import to_bitget_symbol, to_canonical_symbol
from src.exchanges.kis.adapter import KISAdapter
from src.foundation.market_data.contracts.v1 import Venue
from src.foundation.market_data.domain.reference.symbol_normalizer import (
    SymbolNormalizationError,
    to_canonical,
    to_venue,
)

TOKEN_RESPONSE = {"access_token": "tok-1", "access_token_token_expired": "2099-01-01 00:00:00"}


def _make_bitget_adapter(handler) -> BitgetAdapter:
    transport = httpx.MockTransport(handler)
    client = httpx.AsyncClient(base_url="https://api.bitget.com", transport=transport)
    return BitgetAdapter("key", "secret", "passphrase", demo_mode=True, http_client=client)


def _make_kis_adapter(handler) -> KISAdapter:
    def _route(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/oauth2/tokenP":
            return httpx.Response(200, json=TOKEN_RESPONSE)
        return handler(request)

    transport = httpx.MockTransport(_route)
    client = httpx.AsyncClient(
        base_url="https://openapivts.koreainvestment.com:29443", transport=transport
    )
    return KISAdapter("app", "secret", "12345678", "01", is_paper_trading=True, http_client=client)


def _fail_if_called(_request: httpx.Request) -> httpx.Response:
    pytest.fail("symbol validation must reject before any HTTP request is made")


# ---------- Bitget symbols.py delegates to symbol_normalizer(LA-7) ----------


def test_to_bitget_symbol_matches_normalizer() -> None:
    assert to_bitget_symbol("BTC/USDT") == to_venue(Venue.BITGET, "BTC/USDT") == "BTCUSDT"


def test_to_canonical_symbol_matches_normalizer() -> None:
    assert to_canonical_symbol("BTCUSDT") == to_canonical(Venue.BITGET, "BTCUSDT") == "BTC/USDT"


def test_to_bitget_symbol_unknown_quote_raises() -> None:
    with pytest.raises(SymbolNormalizationError):
        to_bitget_symbol("BTC/XYZ")


def test_to_canonical_symbol_unknown_quote_raises() -> None:
    with pytest.raises(SymbolNormalizationError):
        to_canonical_symbol("BTCXYZ")


# ---------- Bitget market data round-trips through the shared normalizer ----------


async def test_bitget_get_ticker_request_uses_normalizer_raw_symbol():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.params["symbol"] == "BTCUSDT"
        return httpx.Response(
            200,
            json={
                "code": "00000",
                "msg": "success",
                "requestTime": 1,
                "data": [
                    {
                        "symbol": "BTCUSDT",
                        "lastPr": "80000",
                        "bidPr": "79990",
                        "askPr": "80010",
                        "baseVolume": "100",
                        "ts": "1000",
                    }
                ],
            },
        )

    adapter = _make_bitget_adapter(handler)
    ticker = await adapter.get_ticker("BTC/USDT")

    assert ticker.symbol == "BTC/USDT"


# ---------- KIS market data delegates KRX validation to symbol_normalizer(LA-7) ----------


async def test_kis_get_ticker_valid_krx_code_passes_through():
    def handler(request: httpx.Request) -> httpx.Response:
        if "inquire-price" in request.url.path:
            return httpx.Response(
                200,
                json={
                    "rt_cd": "0",
                    "msg1": "ok",
                    "output": {"stck_prpr": "70000", "acml_vol": "1"},
                },
            )
        return httpx.Response(
            200, json={"rt_cd": "0", "msg1": "ok", "output1": {"askp1": "70100", "bidp1": "69900"}}
        )

    adapter = _make_kis_adapter(handler)
    ticker = await adapter.get_ticker("005930")

    assert ticker.symbol == "005930"


async def test_kis_get_ticker_malformed_symbol_rejected_before_request():
    adapter = _make_kis_adapter(_fail_if_called)

    with pytest.raises(SymbolNormalizationError):
        await adapter.get_ticker("BTC/USDT")


async def test_kis_get_orderbook_malformed_symbol_rejected_before_request():
    adapter = _make_kis_adapter(_fail_if_called)

    with pytest.raises(SymbolNormalizationError):
        await adapter.get_orderbook("AAPL")


async def test_kis_get_ohlcv_malformed_symbol_rejected_before_request():
    adapter = _make_kis_adapter(_fail_if_called)

    with pytest.raises(SymbolNormalizationError):
        await adapter.get_ohlcv("12345", "1d")


# ---------- 실패주입 — 입력검증과 무관한 실 어댑터 결함(거래소 응답 손상) ----------
#
# 아래 테스트들은 전부 symbol_normalizer 검증을 통과하는 유효한 심볼("005930")을
# 쓴다 — 실패 원인이 심볼 형식이 아니라 거래소 응답 자체(필드 누락)임을
# 보장한다. get_ohlcv 계열(일봉/분봉)은 get_ticker/get_orderbook과 달리
# 원래 응답 파싱을 try/except로 감싸지 않아 bare KeyError가 그대로
# 새어나갔다 — market_data_mixin.py에서 함께 고쳤다.


async def test_kis_get_ticker_malformed_response_missing_price_field_raises_fatal_not_keyerror():
    def handler(request: httpx.Request) -> httpx.Response:
        if "inquire-price" in request.url.path:
            return httpx.Response(
                200,
                json={"rt_cd": "0", "msg1": "ok", "output": {"acml_vol": "1"}},  # stck_prpr 누락
            )
        return httpx.Response(
            200, json={"rt_cd": "0", "msg1": "ok", "output1": {"askp1": "70100", "bidp1": "69900"}}
        )

    adapter = _make_kis_adapter(handler)

    with pytest.raises(FatalExchangeError):
        await adapter.get_ticker("005930")


async def test_kis_get_orderbook_malformed_response_missing_field_raises_fatal_not_keyerror():
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"rt_cd": "0", "msg1": "ok", "output1": {"bidp1": "69900"}},  # bidp_rsqn1 누락
        )

    adapter = _make_kis_adapter(handler)

    with pytest.raises(FatalExchangeError):
        await adapter.get_orderbook("005930")


async def test_kis_get_ohlcv_daily_malformed_response_missing_field_raises_fatal_not_keyerror():
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "rt_cd": "0",
                "msg1": "ok",
                "output2": [{"stck_bsop_date": "20260101"}],  # OHLCV 필드 전부 누락
            },
        )

    adapter = _make_kis_adapter(handler)

    with pytest.raises(FatalExchangeError):
        await adapter.get_ohlcv("005930", "1d")


async def test_kis_get_ohlcv_intraday_malformed_response_missing_field_raises_fatal_not_keyerror():
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "rt_cd": "0",
                "msg1": "ok",
                "output2": [{"stck_cntg_hour": "093000"}],  # OHLCV 필드 전부 누락
            },
        )

    adapter = _make_kis_adapter(handler)

    with pytest.raises(FatalExchangeError):
        await adapter.get_ohlcv("005930", "1m")


# ---------- 수치 성능 단언 — symbols.py 순수 함수 처리량(정규화 배율) ----------

_N_SYMBOLS = 2000
_N_ITERATIONS = 5
_RAW_SYMBOLS = [f"SYM{i}USDT" for i in range(_N_SYMBOLS)]
_CANONICAL_SYMBOLS = [f"SYM{i}/USDT" for i in range(_N_SYMBOLS)]


@pytest.mark.perf
def test_bitget_symbol_conversion_throughput_bounded_vs_trivial_baseline() -> None:
    """symbols.py는 I/O 없는 순수 함수라 처리량 회귀는 벤치마크로만 드러난다.
    공유 CI 환경에서 절대 ms 임계는 상시 적색을 낳으므로(선례:
    test_provider_spi_deepen_2885.py), 같은 프로세스가 방금 측정한 구조적으로
    동등한 트리비얼 문자열 조작 베이스라인 대비 정규화 배율을 쓴다."""
    to_bitget_symbol("BTC/USDT")  # 워밍업 — import/최초 호출 1회성 비용 배제
    to_canonical_symbol("BTCUSDT")

    def run_conversion() -> None:
        for _ in range(_N_ITERATIONS):
            for raw in _RAW_SYMBOLS:
                to_canonical_symbol(raw)
            for canonical in _CANONICAL_SYMBOLS:
                to_bitget_symbol(canonical)

    def run_baseline() -> None:
        for _ in range(_N_ITERATIONS):
            for raw in _RAW_SYMBOLS:
                if raw.endswith("USDT"):
                    _ = raw[: -len("USDT")]
            for canonical in _CANONICAL_SYMBOLS:
                _, _, _ = canonical.partition("/")

    baseline_start = time.perf_counter()
    run_baseline()
    baseline_seconds = time.perf_counter() - baseline_start

    conversion_start = time.perf_counter()
    run_conversion()
    conversion_seconds = time.perf_counter() - conversion_start

    assert baseline_seconds > 0.0
    ratio = conversion_seconds / baseline_seconds
    budget_ratio = 20.0  # to_venue/to_canonical는 quote 목록 정렬+순회를 더 하므로
    # 트리비얼 endswith/partition보다 근본적으로 느리다.
    print(
        f"\nBitget symbol conversion throughput vs trivial baseline: "
        f"n_symbols={_N_SYMBOLS} n_iterations={_N_ITERATIONS} "
        f"baseline={baseline_seconds * 1000:.1f}ms conversion={conversion_seconds * 1000:.1f}ms "
        f"ratio={ratio:.2f} (budget={budget_ratio})"
    )
    assert ratio < budget_ratio, (
        f"to_bitget_symbol/to_canonical_symbol이 베이스라인 대비 {ratio:.2f}배로 "
        f"회귀했습니다(예산 {budget_ratio}배) — symbol_normalizer 위임 경로에 "
        "의도치 않은 무거운 연산이 섞였을 가능성."
    )


# ---------- 게이트/CI 적색선 재현 — 검증 위임을 되돌리면 fail-closed 테스트가 뒤집히는가 ----------
#
# LA-19가 막으려던 바로 그 회귀 — 어댑터가 `symbol_normalizer` 위임을 버리고
# 검증 없는 변환을 자체 재구현 — 를 자식 pytest 프로세스 안에서만 소스
# 문자열 치환으로 주입한다(프로덕션 소스는 그대로). test_provider_spi_deepen_2885.py
# 선례와 동일 기법.

_THIS_TESTFILE = "tests/unit/exchanges/test_symbol_canonicalization.py"

_BITGET_DELEGATION_GUARD = "    return _to_venue(Venue.BITGET, canonical_symbol)\n"
_BITGET_DELEGATION_MUTATED = '    return canonical_symbol.replace("/", "")\n'

_KIS_TICKER_VALIDATION_GUARD = (
    "        symbol = _to_canonical_symbol(Venue.KIS_KRX, symbol)\n"
    "        price_raw = await self._request(\n"
)
_KIS_TICKER_VALIDATION_MUTATED = "        price_raw = await self._request(\n"


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


def test_pytest_gate_turns_red_when_bitget_symbol_delegation_is_bypassed(tmp_path: Path) -> None:
    """task 지침 — "구현을 되돌리면 테스트가 FAIL로 돌아오는가" 실측. 미상
    quote를 거부하는 `to_bitget_symbol`이 `symbol_normalizer` 위임 대신
    검증 없는 자체 문자열 처리로 되돌아가면(LA-19 이전 상태), 미상 quote
    거부 테스트가 green(1 passed)에서 red(1 failed)로 뒤집혀야 한다."""
    module = importlib.import_module("src.exchanges.bitget.symbols")
    assert Path(module.__file__).read_text(encoding="utf-8").count(_BITGET_DELEGATION_GUARD) == 1

    target_test = f"{_THIS_TESTFILE}::test_to_bitget_symbol_unknown_quote_raises"
    baseline = _run_pytest_node(target_test)
    assert baseline.returncode == 0, baseline.stdout + baseline.stderr
    assert "1 passed" in baseline.stdout

    plugin_name = "_mutate_bitget_symbol_delegation"
    plugin_path = tmp_path / f"{plugin_name}.py"
    plugin_path.write_text(
        _source_mutation_plugin_source(
            "src.exchanges.bitget.symbols", _BITGET_DELEGATION_GUARD, _BITGET_DELEGATION_MUTATED
        ),
        encoding="utf-8",
    )

    mutated = _run_pytest_node(target_test, plugin_name=plugin_name, plugin_dir=tmp_path)
    assert mutated.returncode != 0, mutated.stdout + mutated.stderr
    assert "1 passed" not in mutated.stdout
    assert "1 failed" in mutated.stdout


def test_pytest_gate_turns_red_when_kis_get_ticker_symbol_validation_is_removed(
    tmp_path: Path,
) -> None:
    """KIS `get_ticker`가 `symbol_normalizer` 검증 호출을 건너뛰면(형식이
    잘못된 심볼이 그대로 거래소로 새어나가는 회귀), 호출 전 거부를 확인하는
    기존 테스트가 green에서 red로 뒤집혀야 한다."""
    module = importlib.import_module("src.exchanges.kis.market_data_mixin")
    source = Path(module.__file__).read_text(encoding="utf-8")
    assert source.count(_KIS_TICKER_VALIDATION_GUARD) == 1

    target_test = f"{_THIS_TESTFILE}::test_kis_get_ticker_malformed_symbol_rejected_before_request"
    baseline = _run_pytest_node(target_test)
    assert baseline.returncode == 0, baseline.stdout + baseline.stderr
    assert "1 passed" in baseline.stdout

    plugin_name = "_mutate_kis_ticker_validation"
    plugin_path = tmp_path / f"{plugin_name}.py"
    plugin_path.write_text(
        _source_mutation_plugin_source(
            "src.exchanges.kis.market_data_mixin",
            _KIS_TICKER_VALIDATION_GUARD,
            _KIS_TICKER_VALIDATION_MUTATED,
        ),
        encoding="utf-8",
    )

    mutated = _run_pytest_node(target_test, plugin_name=plugin_name, plugin_dir=tmp_path)
    assert mutated.returncode != 0, mutated.stdout + mutated.stderr
    assert "1 passed" not in mutated.stdout
    assert "1 failed" in mutated.stdout
