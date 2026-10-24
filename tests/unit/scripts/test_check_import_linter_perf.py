"""scripts/check_import_linter.py 성능 테스트 -- RATCHET-2, task-3256/task-16582.

상대 CPU/wall-clock 예산 단언과 SCAN_WORKERS 병렬화 검증.
주 테스트 파일(test_check_import_linter.py)과 동일한 fixture/module 로딩을 공유.

task-9114 ([health:ci_red_systemic] import_linter 24h 4회 반복 조사): 반복된
task-8752/8845/8930은 서로 다른 위반이 아니라 같은 콜드체크아웃 디스크 I/O
타임아웃 회귀였다 -- `test_build_graph_throughput_budget`이 캐시된(즉시
반환되는) tmp_path 파일만 CPU 모드로 측정해 실제 병목(블로킹 디스크 read)을
전혀 재현하지 못했기 때문에 매번 production CI에서만 발견됐다.
`test_build_graph_overlaps_io_bound_reads`가 그 격차를 인위적 read 지연으로
메운다.
"""

from __future__ import annotations

import importlib.util
import sys
import time
from pathlib import Path
from types import ModuleType

import pytest

from tests._perf.relative_budget import RelativeBudget

_ROOT = Path(__file__).resolve().parents[3]
_SCRIPTS_DIR = _ROOT / "scripts"


def _load_module(name: str, path: Path) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


cil = _load_module("check_import_linter", _SCRIPTS_DIR / "check_import_linter.py")


def _touch_pkg(tmp_path: Path, *parts: str) -> None:
    for i in range(1, len(parts) + 1):
        pkg_dir = tmp_path.joinpath(*parts[:i])
        pkg_dir.mkdir(parents=True, exist_ok=True)
        init = pkg_dir / "__init__.py"
        if not init.exists():
            init.write_text("", encoding="utf-8")


def _write(tmp_path: Path, relative: str, content: str) -> Path:
    path = tmp_path / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# 성능 단언
# ---------------------------------------------------------------------------


@pytest.mark.perf
def test_build_graph_throughput_budget(tmp_path: Path) -> None:
    """300개 모듈 임포트 그래프 구성이 상대 CPU 예산 안에 끝난다."""
    _touch_pkg(tmp_path, "src", "foundation")
    for i in range(300):
        _write(
            tmp_path,
            f"src/foundation/mod_{i}.py",
            f"import json\nimport src.foundation.mod_{(i + 1) % 300}\n",
        )

    budget = RelativeBudget()
    graph_holder: dict[str, dict[str, set[str]]] = {}

    def _build() -> None:
        graph_holder["graph"] = cil.build_graph(tmp_path)

    # The old 5-second wall-clock limit failed on slower CI hosts (7.61s)
    # without a graph-builder regression.  100x calibration is the equivalent
    # of the historical 5s budget on the observed ~50ms calibration sample,
    # with enough margin for normal runner variance while retaining a hard cap.
    sample = budget.measure(_build, mode="cpu", n=3, warmup=1)
    graph = graph_holder["graph"]

    assert len(graph) >= 300
    assert sample.ratio < 100.0, budget.describe(sample, max_ratio=100.0)


@pytest.mark.parametrize("calibration_seconds", [0.05, 0.2, 1.0])
def test_build_graph_throughput_budget_detection_fails_on_10x_delay(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, calibration_seconds: float
) -> None:
    """실제 그래프 경로의 10배 지연을 호스트 속도와 무관하게 검출한다."""
    _touch_pkg(tmp_path, "src", "foundation")
    for i in range(300):
        _write(
            tmp_path,
            f"src/foundation/mod_{i}.py",
            f"import json\nimport src.foundation.mod_{(i + 1) % 300}\n",
        )

    budget = RelativeBudget()
    original_read_file = cil._read_file
    elapsed = 0.0
    slowdown = 1.0
    reads = 0

    # This is a detector test, not a host benchmark (covered above). Fixed
    # sleep / real calibration ratios fall below 100 on contended runners.
    # Advance a local CPU clock at the real read boundary instead; do not
    # stub measure(), the graph builder, or the budget assertion.
    def _clock(mode: str):
        assert mode == "cpu"
        return lambda: elapsed

    def _calibrate() -> None:
        nonlocal elapsed
        elapsed += calibration_seconds

    def _read_file(path: Path) -> str:
        nonlocal elapsed, reads
        text = original_read_file(path)
        elapsed += calibration_seconds * 20 / 302 * slowdown
        reads += 1
        return str(text)

    monkeypatch.setattr(budget, "_clock", _clock)
    monkeypatch.setattr(cil, "_read_file", _read_file)
    monkeypatch.setattr(cil, "SCAN_WORKERS", 1)

    def _build() -> None:
        graph = cil.build_graph(tmp_path)
        assert len(graph) == 302
        assert "src.foundation.mod_1" in graph["src.foundation.mod_0"]

    def _check_budget():
        return budget.assert_within(
            _build, max_ratio=100.0, mode="cpu", n=1, warmup=0,
            calibration_n=1, calibration_fn=_calibrate,
        )

    sample = _check_budget()
    assert sample.ratio == pytest.approx(20.0)
    assert reads == 302
    slowdown = 10.0
    with pytest.raises(AssertionError, match=r"ratio=200\.000x budget<100\.000x"):
        _check_budget()
    assert reads == 604


@pytest.mark.perf
def test_build_graph_overlaps_io_bound_reads(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """실제 병목 재현: `_read_file`에 인위적 디스크 latency를 주입해 build_graph의
    ThreadPoolExecutor가 그 대기를 실제로 겹쳐 처리하는지 검증한다.

    task-8752/8845/8930이 3회 재발한 근본 원인은 위의 `test_build_graph_throughput_budget`이
    tmp_path의 캐시된(즉시 반환) 파일만 CPU 모드로 재고, 콜드체크아웃의 블로킹 disk read
    자체는 전혀 흉내내지 않았다는 것 -- 그래서 이 병목군의 회귀(SCAN_WORKERS 축소나
    직렬화로의 회귀 포함)가 로컬 게이트를 항상 통과하고 production CI에서만 드러났다.
    이 테스트는 SCAN_WORKERS=1(직렬)과 실제 설정값을 같은 인위 지연 하에서 비교해,
    스레드풀이 I/O 대기를 겹치지 못하는 회귀를 로컬에서 즉시 잡는다.

    raw perf_counter() 단언을 RelativeBudget(mode="wall")로 전환(task-11827).
    """
    n_files = 60
    _touch_pkg(tmp_path, "src", "foundation")
    for i in range(n_files):
        _write(tmp_path, f"src/foundation/mod_{i}.py", "import json\n")

    delay_s = 0.01
    real_read_file = cil._read_file

    def _slow_read_file(path: Path) -> str:
        time.sleep(delay_s)
        return str(real_read_file(path))

    monkeypatch.setattr(cil, "_read_file", _slow_read_file)

    budget = RelativeBudget()

    def _build(workers: int) -> list[str]:
        monkeypatch.setattr(cil, "SCAN_WORKERS", workers)
        return list(cil.build_graph(tmp_path).keys())

    # Use RelativeBudget.measure(mode="wall") for I/O-bound measurement.
    # batch=N groups calls so total time exceeds the ~15.6ms process_time tick
    # quantization; wall mode preserves sleep() duration that CPU mode would miss.
    serial_sample = budget.measure(lambda: _build(workers=1), mode="wall", n=3, warmup=1)
    parallel_sample = budget.measure(lambda: _build(workers=24), mode="wall", n=3, warmup=1)

    # Serial pays ~n_files * delay_s (0.6s here); a working pool overlaps that wait so
    # parallel should land well under half of it -- generous enough margin for CI jitter
    # while still catching a de-parallelization regression (e.g. SCAN_WORKERS silently
    # dropped to 1, or a future refactor back to a plain serial loop).
    assert parallel_sample.op_ms < serial_sample.op_ms / 2, (
        f"parallel={parallel_sample.op_ms:.3f}ms serial={serial_sample.op_ms:.3f}ms -- "
        "I/O reads are not overlapping (this is the exact defect class that reopened "
        "esc-ci-import_linter three times: task-8752/8845/8930)"
    )


def test_resolve_scan_workers_uses_default_without_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("AIOS_CI_SCAN_WORKERS", raising=False)
    assert cil._resolve_scan_workers(16) == 16


def test_resolve_scan_workers_honors_positive_int_override(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AIOS_CI_SCAN_WORKERS", "4")
    assert cil._resolve_scan_workers(16) == 4


def test_resolve_scan_workers_falls_back_on_non_int_override(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AIOS_CI_SCAN_WORKERS", "not-a-number")
    assert cil._resolve_scan_workers(16) == 16


def test_resolve_scan_workers_falls_back_on_non_positive_override(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AIOS_CI_SCAN_WORKERS", "0")
    assert cil._resolve_scan_workers(16) == 16
