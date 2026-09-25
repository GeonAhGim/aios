"""DSL 컴파일 벤치 — ADR-2026-09-09-C Decision 1 "DSL 컴파일 300ms" 예산 재현.

`compile_source`(DSL-2~7 파이프라인, 순수·no I/O)를 대표 전략 스크립트 N개에 대해
반복 실행해 p50/p95/max(ms)를 재고 `docs/perf/dsl_compile_bench.json`에 쓴다.
측정 방법은 `tests/unit/core/script/test_compile.py::test_compile_p95_latency_within_adr_budget`
과 동일하다(같은 percentile 방식) — 이 스크립트는 그 단언을 CI 밖에서 재현 가능한
산출물로 남기는 것이 목적이다.

사용: `python scripts/bench/dsl_compile_bench.py [--out PATH] [--iterations N]`.
DB/네트워크 불필요. 종료코드는 항상 0 — 예산 초과는 `passed=false`로 JSON에
기록되고, 게이트 판정은 `closeout_check.py`가 그 필드를 읽어서 한다.
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts.bench._common import ROOT, summarize, write_result  # noqa: E402
from src.core.script.artifact.compile import compile_source  # noqa: E402

BUDGET_MS = 300.0
REGISTRY_VERSION = "r" * 64
DEFAULT_OUT = ROOT / "docs" / "perf" / "dsl_compile_bench.json"

# 대표 전략 스크립트 N개 — 지표 개수·조건 중첩·plot/order 수를 달리해 "대표성"을 확보한다.
SAMPLE_SCRIPTS: dict[str, str] = {
    "rsi_threshold": (
        "input length: int = 14\n"
        "input close: series<float> = 0\n"
        "let rsi_val = ta.rsi(close, length)\n"
        "let prev = close[1]\n"
        "signal go_long = rsi_val < 30 and close > prev\n"
        "plot(rsi_val, 1)\n"
        "order(buy, 1, 2) when go_long"
    ),
    "sma_cross": (
        "input fast: int = 10\n"
        "input slow: int = 30\n"
        "input close: series<float> = 0\n"
        "let fast_ma = ta.sma(close, fast)\n"
        "let slow_ma = ta.sma(close, slow)\n"
        "signal go_long = fast_ma > slow_ma and fast_ma[1] <= slow_ma[1]\n"
        "signal go_short = fast_ma < slow_ma and fast_ma[1] >= slow_ma[1]\n"
        "plot(fast_ma, 1)\n"
        "plot(slow_ma, 2)\n"
        "order(buy, 1, 3) when go_long\n"
        "order(sell, 1, 3) when go_short"
    ),
    "macd_multi_filter": (
        "input close: series<float> = 0\n"
        "input volume: series<float> = 0\n"
        "let macd_line = ta.ema(close, 12) - ta.ema(close, 26)\n"
        "let signal_line = ta.ema(macd_line, 9)\n"
        "let hist = macd_line - signal_line\n"
        "let vol_ma = ta.sma(volume, 20)\n"
        "let rsi_val = ta.rsi(close, 14)\n"
        "signal bullish = hist > 0 and hist[1] <= 0 and volume > vol_ma and rsi_val < 70\n"
        "signal bearish = hist < 0 and hist[1] >= 0 and volume > vol_ma and rsi_val > 30\n"
        "plot(macd_line, 1)\n"
        "plot(signal_line, 1)\n"
        "plot(hist, 2)\n"
        "order(buy, 1, 4) when bullish\n"
        "order(sell, 1, 4) when bearish"
    ),
}


def _latencies_ms(source: str, iterations: int) -> list[float]:
    samples: list[float] = []
    for _ in range(iterations):
        started = time.perf_counter()
        compile_source(source, registry_version=REGISTRY_VERSION)
        samples.append((time.perf_counter() - started) * 1000)
    return samples


def run(iterations: int) -> list[float]:
    all_samples: list[float] = []
    for source in SAMPLE_SCRIPTS.values():
        all_samples.extend(_latencies_ms(source, iterations))
    return all_samples


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--iterations", type=int, default=30, help="스크립트당 반복 횟수")
    args = parser.parse_args(argv)

    samples = run(args.iterations)
    result = summarize("dsl_compile_bench", samples, BUDGET_MS)
    write_result(result, args.out)

    status = "PASS" if result.passed else "FAIL"
    print(
        f"[dsl_compile_bench] {status} p50={result.p50_ms:.2f}ms p95={result.p95_ms:.2f}ms "
        f"max={result.max_ms:.2f}ms budget<{BUDGET_MS:.0f}ms n={result.samples} -> {args.out}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
