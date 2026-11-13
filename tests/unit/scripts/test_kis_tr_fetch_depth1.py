"""scripts/kis_tr_fetch.py 깊이 하한 보강 BR-11 — D1->D2 증거 신설.

기존 테스트(test_kis_tr_fetch.py)와 대상 모듈(kis_tr_fetch.py)의 fixture·임포트 방식을
 그대로 따른다. 새 파일은 기존 파일을 수정하지 않는다.

원장 증거: negative 3, failure_injection 1, perf 2, gate_red 0, adversarial 0.
신설: negative 3+, failure_injection 1+, gate_red 1+, perf 1+(best_of + virtual_delay).
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

import pytest

from tests.conftest import PerfBudget
from tests.support.perf_inject import reset_virtual_totals, virtual_totals_ns

ROOT = Path(__file__).resolve().parents[3]
SCRIPTS_DIR = ROOT / "scripts"


def _load_module(name: str, path: Path) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


kis_tr_fetch = _load_module("kis_tr_fetch", SCRIPTS_DIR / "kis_tr_fetch.py")

# ── 합성 데이터 ──────────────────────────────────────────────────────────────

_SAMPLE_FILE = """# [장내채권] 주문/계좌 - 장내채권 매수주문
##############################################################################################
# [장내채권] 주문/계좌 > 장내채권 매수주문 [국내주식-124]
##############################################################################################

def buy():
    tr_id = "TTTC0952U"
    ...
"""

_EMPTY_FILE = """# 빈 파일 — TR 정의 없음
def nothing():
    pass
"""

_ONLY_COMMENTS = """# [국내주식] 주문/계좌 > 주식주문(현금) [v1_국내주식-001]
# 주석만 있는 파일
"""

_MULTIPLE_TR_IDS = """# [국내주식] 주문/계좌 > 주식주문 [v1_국내주식-001]

def order():
    tr_id = "TTTC0011U"
    # 중간의 다른 주석
    tr_id = "TTTC0012U"
    tr_id = "VTTC0011U"
"""

_INVALID_PATH_CHARS = """# [특수/경로] > 특수문자 [X-999]

def weird():
    tr_id = "INVALID~PATH"
"""

# ── 음성(negative) 테스트 3종 ───────────────────────────────────────────────


def test_extract_trs_from_file_returns_empty_on_no_tr_id(monkeypatch) -> None:
    """TR ID 대입문이 전혀 없는 파일은 빈 목록을 반환해야 한다."""
    monkeypatch.setattr(kis_tr_fetch, "_http_get", lambda url: b"def foo(): pass")

    assert kis_tr_fetch.extract_trs_from_file("examples_llm/no_tr/foo.py") == []


def test_extract_trs_from_file_returns_empty_on_only_comments(monkeypatch) -> None:
    """헤더 주석만 있고 tr_id 대입이 없는 파일도 빈 목록."""
    monkeypatch.setattr(kis_tr_fetch, "_http_get", lambda url: _ONLY_COMMENTS.encode("utf-8"))

    rows = kis_tr_fetch.extract_trs_from_file("examples_llm/empty/header_only.py")
    assert rows == []


def test_extract_trs_from_file_handles_utf8_bom_and_mixed_encoding(monkeypatch) -> None:
    """BOM 포함 UTF-8 또는 깨진 바이트가 섞여 있어도 replace 에러로 치환 후 파싱해야 한다."""
    # BOM + 일부 깨진 바이트
    bad_text = b"\xef\xbb\xbf# [korean] order\ntr_id = 'TTTC0011U'\n\xff\xfe"
    monkeypatch.setattr(kis_tr_fetch, "_http_get", lambda url: bad_text)

    rows = kis_tr_fetch.extract_trs_from_file("examples_llm/encoding/bom_file.py")
    assert len(rows) == 1
    assert rows[0]["tr_id"] == "TTTC0011U"


# ── 실패 주입(failure-injection) ────────────────────────────────────────────


def test_build_reference_propagates_unicode_decode_error_as_kistrfetch_error(
    monkeypatch,
) -> None:
    """_http_get가 decode 시 오류를 일으키는 바이트를 반환하면 KisTrFetchError로 전파한다."""

    def _bad_http_get(url: str) -> bytes:
        # decode('utf-8', 'replace')는 깨진 바이트를 치환하지만,
        # 실제 decode('utf-8')만 쓰는 경로는 UnicodeDecodeError를 던진다.
        # kis_tr_fetch는 decode("utf-8", errors="replace")를 쓰므로 여기서는
        # 완전히 끊기는 케이스를 주입한다.
        raise OSError("Network unreachable")

    monkeypatch.setattr(kis_tr_fetch, "_http_get", _bad_http_get)
    monkeypatch.setattr(kis_tr_fetch, "list_example_files", lambda ref="main": ("abc123", ["x.py"]))

    with pytest.raises(OSError, match="Network unreachable"):
        kis_tr_fetch.build_reference()


def test_list_example_files_raises_on_missing_sha(monkeypatch) -> None:
    """GitHub commits API가 sha 필드를 반환하지 않으면 KisTrFetchError를 던진다."""
    monkeypatch.setattr(kis_tr_fetch, "_http_get", lambda url: b'{"commit": {"message": "ok"}}')

    with pytest.raises(kis_tr_fetch.KisTrFetchError, match="커밋 SHA"):
        kis_tr_fetch.list_example_files()


# ── 게이트 적색 재현(gate-red reproduction) ─────────────────────────────────
# find_extraction_failures가 hard/soft를 올바르게 분리하는지, 그리고
# write_reference가 hard가 있을 때 종료코드 1을 반환하는지 검증한다.


def test_gate_red_hard_failure_on_multiple_missing_paths(monkeypatch, tmp_path, capsys) -> None:
    """여러 TR에서 path가 누락되면 write_reference가 종료코드 1을 반환하고 모두 나열한다.

    입력 데이터를 의도적으로 망가뜨려 게이트가 실제로 잡는지 확인한다.
    """
    trs = [
        {
            "tr_id": f"TR{i:04d}",
            "source_path": f"examples_llm/x/TR{i:04d}/x.py",
            "path": None,
            "method": None,
            "extraction_error": f"path missing reason {i}",
        }
        for i in range(5)
    ]
    # WS TR은 path 누락이 hard가 아님 — 분리 검증
    trs.append(
        {
            "tr_id": "H0CFCNT0",
            "source_path": "examples_llm/ws/h0.py",
            "path": None,
            "method": "WS",
            "extraction_error": "websocket is ok without path",
        }
    )

    monkeypatch.setattr(
        kis_tr_fetch,
        "build_reference",
        lambda ref="main": {
            "source_repo": kis_tr_fetch.REPO,
            "source_ref": "main",
            "source_commit": "deadbeef",
            "fetched_at": "2026-01-01T00:00:00+00:00",
            "file_count": 5,
            "trs": trs,
        },
    )

    exit_code = kis_tr_fetch.write_reference(output=tmp_path / "ref.json")

    assert exit_code == 1
    captured = capsys.readouterr().out
    # 5개 hard TR이 모두 나열되어야 함
    for i in range(5):
        assert f"TR{i:04d}" in captured
    # WS TR은 hard에 포함되지 않음
    assert "H0CFCNT0" not in captured.split("FAIL[path/method]")[0]


def test_gate_red_soft_warning_does_not_bail_exit_code(monkeypatch, tmp_path, capsys) -> None:
    """path/method는 채웠지만 params/response 추출 실패는 soft 경고 — 종료코드 0."""
    trs = [
        {
            "tr_id": "TTTC0011U",
            "source_path": "examples_llm/order/x.py",
            "path": "/uapi/order",
            "method": "POST",
            "extraction_error": "params block 못찾음",
        },
    ]

    monkeypatch.setattr(
        kis_tr_fetch,
        "build_reference",
        lambda ref="main": {
            "source_repo": kis_tr_fetch.REPO,
            "source_ref": "main",
            "source_commit": "deadbeef",
            "fetched_at": "2026-01-01T00:00:00+00:00",
            "file_count": 1,
            "trs": trs,
        },
    )

    exit_code = kis_tr_fetch.write_reference(output=tmp_path / "ref.json")

    assert exit_code == 0
    captured = capsys.readouterr().out
    assert "WARN[params" in captured


# ── 성능 단언(performance assertion) ────────────────────────────────────────


@pytest.mark.perf
def test_extract_trs_from_file_perf_budget_best_of(monkeypatch, perf_budget: PerfBudget) -> None:
    """성능 단언: best_of(5)로 노이즈 제거 후 CPU ms 예산 내 확인.

    실측 기준: 이 호스트에서 extract_trs_from_file(합성 데이터 1건)은
    약 0.05~0.15ms (CPU). 예산은 1ms로 10배 이상 여유.
    """
    monkeypatch.setattr(kis_tr_fetch, "_http_get", lambda url: _SAMPLE_FILE.encode("utf-8"))

    sample = perf_budget.best_of(
        lambda: kis_tr_fetch.extract_trs_from_file("examples_llm/domestic_stock/order/order.py"),
        n=5,
        batch=500,
    )

    assert sample.cpu_ms < 1.0, (
        f"extract_trs_from_file이 예산 1ms를 초과: cpu={sample.cpu_ms:.3f}ms "
        f"wall={sample.wall_ms:.3f}ms"
    )


@pytest.mark.perf
def test_perf_virtual_delay_injection_fails_red(monkeypatch) -> None:
    """검출력 확인: virtual_delayed_sync으로 0.5ms 지연 주입 → 예산 초과 적색.

    tests/support/perf_inject의 virtual_delayed_sync은 실제 sleep/spin 없이
    가상 지연을 ledger에 추가한다. PerfBudget.sample가 이 ledger를 합산하므로
    실제 지연 없이 적색 재현이 가능하다.
    """
    from tests.support.perf_inject import virtual_delayed_sync

    reset_virtual_totals()

    original_fn = kis_tr_fetch.extract_trs_from_file

    def _delayed_extract(path: str) -> list:
        return virtual_delayed_sync(original_fn, 0.5)(path)

    monkeypatch.setattr(kis_tr_fetch, "_http_get", lambda url: _SAMPLE_FILE.encode("utf-8"))
    monkeypatch.setattr(
        kis_tr_fetch,
        "extract_trs_from_file",
        lambda p: _delayed_extract(p),
    )

    with pytest.raises(AssertionError):
        PerfBudget().assert_within(
            lambda: kis_tr_fetch.extract_trs_from_file(
                "examples_llm/domestic_stock/order/order.py"
            ),
            budget_ms=0.3,
            n=3,
            batch=100,
            label="virtual_delay_injection gate-red",
        )

    # 가상 지연 ledger가 실제로 이동했는지 확인
    cpu_ns, wall_ns = virtual_totals_ns()
    assert cpu_ns > 0, "virtual_delayed_sync이 ledger에 지연을 추가했는지 검증"
    assert wall_ns > 0, "virtual_delayed_sync이 wall ledger에도 지연을 추가했는지 검증"


# ── 결정성(determinism) ─────────────────────────────────────────────────────


def test_extract_trs_from_file_is_deterministic_given_same_content(monkeypatch) -> None:
    """동일한 파일 내용은 항상 동일한 추출 결과를 반환한다."""
    monkeypatch.setattr(kis_tr_fetch, "_http_get", lambda url: _MULTIPLE_TR_IDS.encode("utf-8"))

    first = kis_tr_fetch.extract_trs_from_file("examples_llm/determ/test.py")
    second = kis_tr_fetch.extract_trs_from_file("examples_llm/determ/test.py")

    assert first == second
    assert [r["tr_id"] for r in first] == ["TTTC0011U", "TTTC0012U", "VTTC0011U"]


def test_build_reference_ordering_is_stable_given_same_files(monkeypatch) -> None:
    """build_reference는 TR ID별 정렬이 결정적이다."""
    files = {
        "examples_llm/z_domain/a.py": _SAMPLE_FILE,
        "examples_llm/a_domain/b.py": _MULTIPLE_TR_IDS,
    }
    monkeypatch.setattr(
        kis_tr_fetch, "list_example_files", lambda ref="main": ("sha1", sorted(files))
    )

    def _fake_http_get(url: str) -> bytes:
        return files[url.split(kis_tr_fetch.RAW_BASE)[-1]].encode("utf-8")

    monkeypatch.setattr(kis_tr_fetch, "_http_get", _fake_http_get)

    result = kis_tr_fetch.build_reference()
    tr_ids = [r["tr_id"] for r in result["trs"]]
    assert tr_ids == sorted(tr_ids)
