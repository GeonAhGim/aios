"""scripts/kis_tr_fetch.py 단위 테스트 — BR-1(ADR-2026-09-06-I D2).

네트워크를 쓰는 `list_example_files`/`build_reference`는 실행하지 않는다(오프라인 CI 원칙).
여기서는 순수 파서 `extract_trs_from_file`의 정규식만 검증한다 — `_http_get`을 monkeypatch해
공식 저장소 예제 파일의 실제 구조(주석 헤더 + `tr_id = "..."` 대입)를 합성 데이터로 재현한다.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

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
