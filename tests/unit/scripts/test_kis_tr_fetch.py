"""scripts/kis_tr_fetch.py 단위 테스트 — BR-1(ADR-2026-09-06-I D2), BR-11(D7).

네트워크를 쓰는 `list_example_files`/`build_reference`는 실제 호출 없이만 실행한다(오프라인
CI 원칙) — `_http_get`을 monkeypatch해 공식 저장소 예제 파일의 실제 구조(주석 헤더 +
`tr_id = "..."` 대입)를 합성 데이터로 재현한다.

DEEPEN(task-2784, docs/audit/DEPTH_L4_BR.md #1928, D1 실측 -> D2 하한): 원 커밋(92c3cac)의
determinism 테스트는 있었으나 (1) 네트워크 중단 같은 실패 주입, (2) 정규식 파싱의 수치
성능/처리량 단언, (3) BR-11이 신설한 fail-closed(path/method 누락 시 종료코드 1) 게이트
경로 검증이 전혀 없었다. 아래 세 그룹이 그 공백을 채운다.
"""
from __future__ import annotations

import importlib.util
import sys
import time
from pathlib import Path
from types import ModuleType

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


kis_tr_fetch = _load_module("kis_tr_fetch", SCRIPTS_DIR / "kis_tr_fetch.py")

_SAMPLE_FILE = """# [장내채권] 주문/계좌 - 장내채권 매수주문
##############################################################################################
# [장내채권] 주문/계좌 > 장내채권 매수주문 [국내주식-124]
##############################################################################################

def buy():
    tr_id = "TTTC0952U"
    ...
"""

_SAMPLE_FILE_REAL_PAPER_SPLIT = """
# [국내주식] 주문/계좌 > 주식주문(현금) [v1_국내주식-001]

def order_cash(real: bool):
    if real:
        tr_id = "TTTC0011U"
        tr_id = "TTTC0012U"
    else:
        tr_id = "VTTC0011U"
        tr_id = "VTTC0012U"
"""


def test_extract_trs_from_file_reads_tr_id_and_header(monkeypatch) -> None:
    monkeypatch.setattr(kis_tr_fetch, "_http_get", lambda url: _SAMPLE_FILE.encode("utf-8"))

    rows = kis_tr_fetch.extract_trs_from_file("examples_llm/domestic_bond/buy/buy.py")

    assert len(rows) == 1
    assert rows[0]["tr_id"] == "TTTC0952U"
    assert rows[0]["domain"] == "domestic_bond"
    assert rows[0]["source_path"] == "examples_llm/domestic_bond/buy/buy.py"
    assert "장내채권 매수주문" in rows[0]["label"]


def test_extract_trs_from_file_collects_all_distinct_tr_ids_in_order(monkeypatch) -> None:
    monkeypatch.setattr(
        kis_tr_fetch, "_http_get", lambda url: _SAMPLE_FILE_REAL_PAPER_SPLIT.encode("utf-8")
    )

    rows = kis_tr_fetch.extract_trs_from_file(
        "examples_llm/domestic_stock/order_cash/order_cash.py"
    )

    assert [r["tr_id"] for r in rows] == ["TTTC0011U", "TTTC0012U", "VTTC0011U", "VTTC0012U"]


def test_extract_trs_from_file_no_tr_id_returns_empty(monkeypatch) -> None:
    monkeypatch.setattr(kis_tr_fetch, "_http_get", lambda url: b"def main():\n    pass\n")

    assert kis_tr_fetch.extract_trs_from_file("examples_llm/auth/auth_token/auth_token.py") == []


def test_build_reference_is_deterministic_given_same_files(monkeypatch) -> None:
    files = {
        "examples_llm/domestic_bond/buy/buy.py": _SAMPLE_FILE,
        "examples_llm/domestic_stock/order_cash/order_cash.py": _SAMPLE_FILE_REAL_PAPER_SPLIT,
    }
    monkeypatch.setattr(
        kis_tr_fetch, "list_example_files", lambda ref="main": ("deadbeef", sorted(files))
    )
    def _fake_http_get(url: str) -> bytes:
        return files[url.split(kis_tr_fetch.RAW_BASE)[-1]].encode("utf-8")

    monkeypatch.setattr(kis_tr_fetch, "_http_get", _fake_http_get)

    first = kis_tr_fetch.build_reference()
    second = kis_tr_fetch.build_reference()

    assert first["trs"] == second["trs"]
    assert [r["tr_id"] for r in first["trs"]] == sorted(r["tr_id"] for r in first["trs"])
    assert first["source_commit"] == "deadbeef"


# ── 실패 주입(failure-injection) ──────────────────────────────────────────
# 이 스크립트의 유일한 I/O는 `_http_get`(GitHub REST + raw.githubusercontent.com)이다.
# 네트워크가 중간에 끊기면 일부 파일만 반영된 손상된 스냅샷을 조용히 쓰는 대신 예외를
# 그대로 전파해야 한다 — ADR D2.4 "조용히 누락되지 않음"은 추출 실패뿐 아니라
# 수집 자체의 실패에도 적용된다.


def test_build_reference_propagates_http_failure_instead_of_writing_partial_snapshot(
    monkeypatch,
) -> None:
    files = [
        "examples_llm/domestic_bond/buy/buy.py",
        "examples_llm/domestic_stock/order_cash/order_cash.py",
    ]
    monkeypatch.setattr(kis_tr_fetch, "list_example_files", lambda ref="main": ("deadbeef", files))

    def _flaky_http_get(url: str) -> bytes:
        if "order_cash" in url:
            raise OSError("Connection reset by peer")
        return _SAMPLE_FILE.encode("utf-8")

    monkeypatch.setattr(kis_tr_fetch, "_http_get", _flaky_http_get)

    with pytest.raises(OSError, match="Connection reset"):
        kis_tr_fetch.build_reference()


def test_list_example_files_raises_on_truncated_tree_response(monkeypatch) -> None:
    """GitHub tree API가 `truncated: true`를 반환하면(저장소가 recursive 조회 한도를
    넘음) 일부 파일 목록만으로 조용히 진행하지 않고 즉시 실패해야 한다."""
    monkeypatch.setattr(
        kis_tr_fetch, "_http_get", lambda url: b'{"truncated": true, "tree": []}'
    )

    with pytest.raises(kis_tr_fetch.KisTrFetchError, match="truncated"):
        kis_tr_fetch.list_example_files()


def test_main_returns_1_and_prints_fail_on_network_error(monkeypatch, capsys) -> None:
    """`write_reference`가 네트워크 예외(`KisTrFetchError`/`OSError`)를 던지면
    `main()`이 이를 삼키지 않고 사유를 출력한 뒤 종료코드 1을 반환해야 한다."""

    def _raise(*args, **kwargs):
        raise kis_tr_fetch.KisTrFetchError("GitHub API 응답 형식이 예상과 다름")

    monkeypatch.setattr(kis_tr_fetch, "write_reference", _raise)

    exit_code = kis_tr_fetch.main()

    assert exit_code == 1
    assert "FAIL" in capsys.readouterr().out


# ── fail-closed 게이트(exit code 1) ───────────────────────────────────────
# BR-11 DoD: 375개 전부에 path·method가 채워져야 한다. 못 채운 TR이 하나라도 있으면
# `write_reference`가 사유를 나열하고 종료코드 1을 반환해야 한다(`main()`이 그대로
# 프로세스 종료코드로 전달) — 원 커밋이 신설한 이 게이트 경로 자체를 검증한다.


def _row(
    tr_id: str,
    path: str | None = None,
    method: str | None = None,
    extraction_error: str | None = None,
) -> dict:
    return {
        "tr_id": tr_id,
        "domain": "domestic_stock",
        "label": "",
        "source_path": f"examples_llm/domestic_stock/{tr_id}/x.py",
        "path": path,
        "method": method,
        "params": [],
        "response": {"kind": "rest", "containers": [], "fields": []},
        "extraction_error": extraction_error,
    }


def test_find_extraction_failures_splits_hard_missing_path_or_method_from_soft_warnings() -> None:
    ok = _row("TTTC0011U", path="/uapi/x", method="GET")
    missing_path = _row("TTTC0012U", method="GET", extraction_error="API_URL 못찾음")
    missing_method = _row("TTTC0013U", path="/uapi/y", extraction_error="_url_fetch 못찾음")
    ws_without_path_is_not_hard = _row("H0CFCNT0", method="WS")
    soft_warning_only = _row(
        "TTTC0014U", path="/uapi/z", method="GET", extraction_error="params={...} 못찾음"
    )

    hard, soft = kis_tr_fetch.find_extraction_failures(
        [ok, missing_path, missing_method, ws_without_path_is_not_hard, soft_warning_only]
    )

    assert {r["tr_id"] for r in hard} == {"TTTC0012U", "TTTC0013U"}
    assert {r["tr_id"] for r in soft} == {"TTTC0014U"}


def test_write_reference_returns_1_and_prints_reason_when_hard_failure_present(
    monkeypatch, tmp_path, capsys
) -> None:
    broken = _row("TTTC9999X", extraction_error="API_URL 상수도 못찾음")
    monkeypatch.setattr(
        kis_tr_fetch,
        "build_reference",
        lambda ref="main": {
            "source_repo": kis_tr_fetch.REPO,
            "source_ref": "main",
            "source_commit": "deadbeef",
            "fetched_at": "2026-01-01T00:00:00+00:00",
            "file_count": 1,
            "trs": [broken],
        },
    )

    exit_code = kis_tr_fetch.write_reference(output=tmp_path / "ref.json")

    assert exit_code == 1
    captured = capsys.readouterr().out
    assert "FAIL[path/method]: TTTC9999X" in captured
    assert "API_URL 상수도 못찾음" in captured
    assert (tmp_path / "ref.json").exists()  # 실패해도 사유 확인용 스냅샷은 남긴다


def test_write_reference_returns_0_when_all_trs_have_path_and_method(
    monkeypatch, tmp_path, capsys
) -> None:
    ok = _row("TTTC0011U", path="/uapi/x", method="GET")
    monkeypatch.setattr(
        kis_tr_fetch,
        "build_reference",
        lambda ref="main": {
            "source_repo": kis_tr_fetch.REPO,
            "source_ref": "main",
            "source_commit": "deadbeef",
            "fetched_at": "2026-01-01T00:00:00+00:00",
            "file_count": 1,
            "trs": [ok],
        },
    )

    exit_code = kis_tr_fetch.write_reference(output=tmp_path / "ref.json")

    assert exit_code == 0
    assert "OK:" in capsys.readouterr().out


# ── 수치 성능 단언 ─────────────────────────────────────────────────────────
# `build_reference`는 375개 예제 파일 각각에 정규식 추출(`extract_trs_from_file`)을
# 돌린다. 순수 문자열/정규식 연산뿐이므로 어떤 CI 머신에서도 초당 수천 회는 나와야
# 정상이다 — 하한을 정상 대비 10배 이상 여유를 둔 값으로 낮게 잡아, 머신 속도 편차로
# 상시 적색이 되지 않으면서도 I/O나 역순회 같은 실질 회귀만 잡는다.


def test_extract_trs_from_file_throughput_meets_batch_generation_floor(monkeypatch) -> None:
    monkeypatch.setattr(
        kis_tr_fetch, "_http_get", lambda url: _SAMPLE_FILE_REAL_PAPER_SPLIT.encode("utf-8")
    )

    n = 1_000
    started = time.perf_counter()
    for _ in range(n):
        kis_tr_fetch.extract_trs_from_file("examples_llm/domestic_stock/order_cash/order_cash.py")
    elapsed = time.perf_counter() - started

    throughput = n / elapsed
    floor_ops_per_sec = 3_000.0
    assert throughput >= floor_ops_per_sec, (
        f"extract_trs_from_file 처리량이 {throughput:.0f} ops/sec으로 하한 "
        f"{floor_ops_per_sec:.0f} ops/sec 밑으로 떨어졌습니다(n={n}, "
        f"elapsed={elapsed * 1000:.1f}ms) — 정규식/문자열 처리 회귀 가능성."
    )
