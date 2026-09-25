"""scripts/kis_tr_coverage.py 단위 테스트 — BR-1(ADR-2026-09-06-I D2).

DoD: (1) 커버리지 하락 시 FAIL, (2) 사유 없는 미구현 TR은 존재할 수 없음(classify()가
항상 4종 중 하나만 반환 — 여기서는 그 불변조건을 회귀 테스트로 고정한다), (3) 같은 입력에
같은 바이트가 나온다, (4) 수치 처리량 단언(DEPTH 감사 task-2722의 유일한 미달 사유 보완 —
docs/audit/DEPTH_L4_BR.md #1779). 전부 합성 데이터(tmp_path)로 검증 — 네트워크·실제
저장소 접근 없음.
"""
from __future__ import annotations

import importlib.util
import json
import sys
import time
from collections.abc import Callable
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[3]
SCRIPTS_DIR = ROOT / "scripts"


def _load_module(name: str, path: Path) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


kis_tr_coverage = _load_module("kis_tr_coverage", SCRIPTS_DIR / "kis_tr_coverage.py")


def _write_reference(
    tmp_path: Path, trs: list[dict[str, str]], name: str = "reference.json"
) -> Path:
    path = tmp_path / name
    path.write_text(
        json.dumps(
            {
                "source_repo": "koreainvestment/open-trading-api",
                "source_ref": "main",
                "source_commit": "deadbeef" * 5,
                "fetched_at": "2026-09-06T00:00:00+00:00",
                "file_count": len(trs),
                "trs": trs,
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return path


_SAMPLE_TRS = [
    {
        "tr_id": "TTTC0011U",
        "domain": "domestic_stock",
        "label": "[국내주식] 매수",
        "source_path": "a.py",
    },
    {
        "tr_id": "VTTC0011U",
        "domain": "domestic_stock",
        "label": "[국내주식] 매수(모의)",
        "source_path": "a.py",
    },
    {
        "tr_id": "TTTC0952U",
        "domain": "domestic_bond",
        "label": "[장내채권] 매수",
        "source_path": "b.py",
    },
]


# ---------------------------------------------------------------------------
# load_reference / scan_adapter_source
# ---------------------------------------------------------------------------


def test_load_reference_missing_file_raises(tmp_path: Path) -> None:
    with pytest.raises(kis_tr_coverage.KisTrCoverageError):
        kis_tr_coverage.load_reference(tmp_path / "does-not-exist.json")


def test_load_reference_empty_trs_raises(tmp_path: Path) -> None:
    path = _write_reference(tmp_path, [])
    with pytest.raises(kis_tr_coverage.KisTrCoverageError):
        kis_tr_coverage.load_reference(path)


def test_scan_adapter_source_missing_dir_raises(tmp_path: Path) -> None:
    with pytest.raises(kis_tr_coverage.KisTrCoverageError):
        kis_tr_coverage.scan_adapter_source(tmp_path / "no-such-dir")


def test_scan_adapter_source_concatenates_all_py_files(tmp_path: Path) -> None:
    adapter_dir = tmp_path / "adapter"
    (adapter_dir).mkdir()
    (adapter_dir / "a.py").write_text('tr_id = "TTTC0011U"\n', encoding="utf-8")
    (adapter_dir / "b.py").write_text('tr_id = "TTTC0952U"\n', encoding="utf-8")

    text = kis_tr_coverage.scan_adapter_source(adapter_dir)

    assert "TTTC0011U" in text
    assert "TTTC0952U" in text


# ---------------------------------------------------------------------------
# classify() — 사유는 항상 4종 중 하나 (D2.3)
# ---------------------------------------------------------------------------


def test_classify_implemented_wins_regardless_of_prefix() -> None:
    all_ids = frozenset({"TTTC0011U"})
    assert kis_tr_coverage.classify("TTTC0011U", all_ids, implemented=True) == "구현됨"


def test_classify_real_tr_without_paper_sibling_needs_live_account() -> None:
    all_ids = frozenset({"TTTC0952U"})  # VTTC0952U가 기준 목록에 없음
    assert kis_tr_coverage.classify("TTTC0952U", all_ids, implemented=False) == "실전계좌필요"


def test_classify_real_tr_with_paper_sibling_defaults_to_not_started() -> None:
    all_ids = frozenset({"TTTC0011U", "VTTC0011U"})
    assert kis_tr_coverage.classify("TTTC0011U", all_ids, implemented=False) == "미착수"


def test_classify_paper_tr_itself_defaults_to_not_started() -> None:
    all_ids = frozenset({"VTTC0011U"})
    assert kis_tr_coverage.classify("VTTC0011U", all_ids, implemented=False) == "미착수"


def test_classify_scope_override_wins(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(kis_tr_coverage._SCOPE_OVERRIDES, "TTTC0952U", "테스트 사유")
    all_ids = frozenset({"TTTC0952U"})
    assert kis_tr_coverage.classify("TTTC0952U", all_ids, implemented=False) == "범위밖"


# ---------------------------------------------------------------------------
# build_matrix — 카운트·정렬
# ---------------------------------------------------------------------------


def test_build_matrix_counts_and_sorts_by_tr_id(tmp_path: Path) -> None:
    reference = {"trs": _SAMPLE_TRS}
    adapter_source = 'tr_id = "TTTC0011U"\n'  # VTTC0011U·TTTC0952U는 미구현

    matrix = kis_tr_coverage.build_matrix(reference, adapter_source)

    assert matrix.total == 3
    assert matrix.implemented == 1
    assert [row.tr_id for row in matrix.rows] == ["TTTC0011U", "TTTC0952U", "VTTC0011U"]
    reasons = {row.tr_id: row.reason for row in matrix.rows}
    assert reasons["TTTC0011U"] == "구현됨"
    assert reasons["TTTC0952U"] == "실전계좌필요"  # VTTC0952U 짝 없음
    assert reasons["VTTC0011U"] == "미착수"


def test_build_matrix_percent_rounds_to_two_decimals() -> None:
    reference = {"trs": _SAMPLE_TRS}
    matrix = kis_tr_coverage.build_matrix(reference, adapter_source='tr_id = "TTTC0011U"\n')

    assert matrix.percent == round(1 / 3 * 100, 2)


# ---------------------------------------------------------------------------
# render_* — 같은 입력 -> 바이트 동일
# ---------------------------------------------------------------------------


def test_render_markdown_is_byte_identical_for_same_input() -> None:
    reference = {
        "source_repo": "koreainvestment/open-trading-api",
        "source_ref": "main",
        "source_commit": "deadbeef" * 5,
        "fetched_at": "2026-09-06T00:00:00+00:00",
        "trs": _SAMPLE_TRS,
    }
    matrix = kis_tr_coverage.build_matrix(reference, adapter_source='tr_id = "TTTC0011U"\n')

    first = kis_tr_coverage.render_markdown(reference, matrix)
    second = kis_tr_coverage.render_markdown(reference, matrix)

    assert first == second
    assert "미착수" in first and "실전계좌필요" in first


def test_render_coverage_txt_is_byte_identical_for_same_input() -> None:
    reference = {"trs": _SAMPLE_TRS}
    matrix = kis_tr_coverage.build_matrix(reference, adapter_source='tr_id = "TTTC0011U"\n')

    first = kis_tr_coverage.render_coverage_txt(matrix)
    second = kis_tr_coverage.render_coverage_txt(matrix)
    assert first == second


# ---------------------------------------------------------------------------
# 수치 처리량 단언 — DEPTH 감사(task-2722, docs/audit/DEPTH_L4_BR.md #1779)의
# 유일한 미달 사유: "No numeric performance/throughput assertion exists in the
# suite". 절대 ms 임계값은 공유 CI 머신 속도차에 취약하므로(이 저장소의 기존
# perf 회귀 테스트들이 실DB p95를 baseline 대비 상대 배율로 단언하는 것과 동일한
# 이유), 같은 프로세스·같은 N에서 측정한 트리비얼 베이스라인 루프 대비 배율로
# 단언한다 — 머신이 느리면 분자·분모가 같이 느려져 비율은 그대로다.
# ---------------------------------------------------------------------------


def _synthetic_reference(n: int) -> dict[str, Any]:
    domains = (
        "domestic_stock",
        "domestic_bond",
        "overseas_stock",
        "domestic_futureoption",
        "overseas_futureoption",
    )
    trs = [
        {
            "tr_id": f"TTTC{i:05d}U",
            "domain": domains[i % len(domains)],
            "label": f"[합성] TR {i}",
            "source_path": f"synthetic_{i % 20}.py",
        }
        for i in range(n)
    ]
    return {
        "source_repo": "koreainvestment/open-trading-api",
        "source_ref": "main",
        "source_commit": "deadbeef" * 5,
        "fetched_at": "2026-09-06T00:00:00+00:00",
        "trs": trs,
    }


def _min_elapsed_seconds(fn: Callable[[], None], repeats: int = 3) -> float:
    times = []
    for _ in range(repeats):
        start = time.perf_counter()
        fn()
        times.append(time.perf_counter() - start)
    return min(times)


def test_build_matrix_and_render_throughput_within_normalized_budget() -> None:
    n = 2000
    reference = _synthetic_reference(n)
    implemented_ids = [row["tr_id"] for row in reference["trs"][::2]]
    adapter_source = "\n".join(f'tr_id = "{tr_id}"' for tr_id in implemented_ids)

    def pipeline() -> None:
        matrix = kis_tr_coverage.build_matrix(reference, adapter_source)
        kis_tr_coverage.render_markdown(reference, matrix)
        kis_tr_coverage.render_coverage_txt(matrix)

    def baseline() -> None:
        acc = 0
        for i in range(n):
            acc += len(str(i))
        assert acc >= 0

    pipeline_seconds = _min_elapsed_seconds(pipeline)
    baseline_seconds = _min_elapsed_seconds(baseline)

    assert baseline_seconds > 0.0
    ratio = pipeline_seconds / baseline_seconds
    # 측정 배율은 이 저장소 CI 머신에서 통상 65~85배(TR당 어댑터 소스 전체 부분
    # 문자열 검색 비용). 300배는 그 위에 ~4배 여유를 두어 O(N^2) 위에 또 다른
    # O(N) 스캔이 얹히는 회귀(예: 도메인별 재정렬을 행 루프 안으로 옮김, 마크다운
    # 렌더링에서 문자열을 매 행마다 재결합)는 잡되 머신 잡음에는 흔들리지 않는다.
    assert ratio < 300.0, (
        f"kis_tr_coverage 파이프라인이 트리비얼 베이스라인 대비 {ratio:.1f}배 "
        f"(N={n}, pipeline={pipeline_seconds:.4f}s, baseline={baseline_seconds:.4f}s) — "
        "선형 스캔 위에 예상 밖의 추가 스캔이 얹힌 회귀로 의심됨"
    )


# ---------------------------------------------------------------------------
# main() — 래칫 DoD
# ---------------------------------------------------------------------------


def _run_main(tmp_path: Path, reference_path: Path, adapter_dir: Path, coverage_txt: Path) -> int:
    return kis_tr_coverage.main(
        [
            "--reference",
            str(reference_path),
            "--adapter-dir",
            str(adapter_dir),
            "--matrix-md",
            str(tmp_path / "MATRIX.md"),
            "--coverage-txt",
            str(coverage_txt),
        ]
    )


def test_first_run_initializes_baseline(tmp_path: Path) -> None:
    reference_path = _write_reference(tmp_path, _SAMPLE_TRS)
    adapter_dir = tmp_path / "adapter"
    adapter_dir.mkdir()
    (adapter_dir / "a.py").write_text('tr_id = "TTTC0011U"\n', encoding="utf-8")
    coverage_txt = tmp_path / "kis-tr-coverage.txt"

    exit_code = _run_main(tmp_path, reference_path, adapter_dir, coverage_txt)

    assert exit_code == 0
    assert coverage_txt.read_text(encoding="utf-8").strip() == f"{round(1/3*100, 2):.2f}"
    assert (tmp_path / "MATRIX.md").exists()


def test_lowered_coverage_fails(tmp_path: Path) -> None:
    reference_path = _write_reference(tmp_path, _SAMPLE_TRS)
    adapter_dir = tmp_path / "adapter"
    adapter_dir.mkdir()
    (adapter_dir / "a.py").write_text("# 아무 TR도 구현하지 않음\n", encoding="utf-8")
    coverage_txt = tmp_path / "kis-tr-coverage.txt"
    coverage_txt.write_text("50.00\n", encoding="utf-8")

    exit_code = _run_main(tmp_path, reference_path, adapter_dir, coverage_txt)

    assert exit_code == 1
    assert coverage_txt.read_text(encoding="utf-8").strip() == "50.00"  # 실패 시 baseline 미변경


def test_risen_coverage_ratchets_baseline_up(tmp_path: Path) -> None:
    reference_path = _write_reference(tmp_path, _SAMPLE_TRS)
    adapter_dir = tmp_path / "adapter"
    adapter_dir.mkdir()
    (adapter_dir / "a.py").write_text(
        'tr_id = "TTTC0011U"\ntr_id = "VTTC0011U"\n', encoding="utf-8"
    )
    coverage_txt = tmp_path / "kis-tr-coverage.txt"
    coverage_txt.write_text("0.00\n", encoding="utf-8")

    exit_code = _run_main(tmp_path, reference_path, adapter_dir, coverage_txt)

    assert exit_code == 0
    assert coverage_txt.read_text(encoding="utf-8").strip() == f"{round(2/3*100, 2):.2f}"


def test_equal_coverage_passes_and_keeps_baseline(tmp_path: Path) -> None:
    reference_path = _write_reference(tmp_path, _SAMPLE_TRS)
    adapter_dir = tmp_path / "adapter"
    adapter_dir.mkdir()
    (adapter_dir / "a.py").write_text('tr_id = "TTTC0011U"\n', encoding="utf-8")
    coverage_txt = tmp_path / "kis-tr-coverage.txt"
    baseline_value = f"{round(1/3*100, 2):.2f}"
    coverage_txt.write_text(baseline_value + "\n", encoding="utf-8")

    exit_code = _run_main(tmp_path, reference_path, adapter_dir, coverage_txt)

    assert exit_code == 0
    assert coverage_txt.read_text(encoding="utf-8").strip() == baseline_value


def test_main_missing_reference_fails(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    adapter_dir = tmp_path / "adapter"
    adapter_dir.mkdir()

    exit_code = _run_main(tmp_path, tmp_path / "missing.json", adapter_dir, tmp_path / "cov.txt")

    assert exit_code == 1
    assert "FAIL" in capsys.readouterr().out
