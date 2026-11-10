"""scripts/merge_openapi_baseline.py -- 기준선에 새 경로·스키마만 추가하는 도구."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.merge_openapi_baseline import MergeError, main, merge_additions, render

BASE = {
    "paths": {"/a": {"get": {"x": 1}}},
    "components": {"schemas": {"A": {"type": "object"}}},
}
CURRENT = {
    "paths": {"/a": {"get": {"x": 999}}, "/b": {"get": {"y": 2}}},
    "components": {"schemas": {"A": {"type": "string"}, "B": {"type": "object"}}},
}


def test_adds_only_requested_path_and_schema_and_keeps_existing_untouched() -> None:
    merged = merge_additions(BASE, CURRENT, ["/b"], ["B"])
    assert merged["paths"]["/b"] == {"get": {"y": 2}}
    assert merged["components"]["schemas"]["B"] == {"type": "object"}
    assert merged["paths"]["/a"] == {"get": {"x": 1}}  # export의 다른 값으로 바뀌지 않는다
    assert merged["components"]["schemas"]["A"] == {"type": "object"}


def test_does_not_mutate_inputs() -> None:
    before = json.dumps(BASE, sort_keys=True)
    merge_additions(BASE, CURRENT, ["/b"], ["B"])
    assert json.dumps(BASE, sort_keys=True) == before


def test_refuses_to_overwrite_existing_path_or_schema() -> None:
    with pytest.raises(MergeError, match="이미 있는 경로"):
        merge_additions(BASE, CURRENT, ["/a"], [])
    with pytest.raises(MergeError, match="이미 있는 스키마"):
        merge_additions(BASE, CURRENT, [], ["A"])


def test_refuses_unknown_items_and_empty_request() -> None:
    with pytest.raises(MergeError, match="경로가 없다"):
        merge_additions(BASE, CURRENT, ["/zzz"], [])
    with pytest.raises(MergeError, match="스키마가 없다"):
        merge_additions(BASE, CURRENT, [], ["ZZZ"])
    with pytest.raises(MergeError, match="하나 이상"):
        merge_additions(BASE, CURRENT, [], [])


def test_render_matches_export_format_and_line_endings() -> None:
    lf = render({"b": 1, "a": "한"}, crlf=False)
    assert lf == b'{\n  "a": "\xed\x95\x9c",\n  "b": 1\n}\n'
    assert render({"a": 1}, crlf=True) == b'{\r\n  "a": 1\r\n}\r\n'


def test_cli_preserves_crlf_and_reports_failure_without_writing(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    baseline = tmp_path / "v1.json"
    baseline.write_bytes(render(BASE, crlf=True))
    current = tmp_path / "cur.json"
    current.write_text(json.dumps(CURRENT), encoding="utf-8")
    original = baseline.read_bytes()

    assert main(["--current", str(current), "--baseline", str(baseline), "--path", "/a"]) == 1
    assert baseline.read_bytes() == original  # 실패하면 파일을 건드리지 않는다
    assert "FAIL" in capsys.readouterr().out

    assert main(["--current", str(current), "--baseline", str(baseline), "--path", "/b"]) == 0
    written = baseline.read_bytes()
    assert b"\r\n" in written and b"\n" not in written.replace(b"\r\n", b"")
    assert json.loads(written.decode("utf-8"))["paths"]["/b"] == {"get": {"y": 2}}


def test_cli_reports_normalization_of_mixed_line_endings(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    baseline = tmp_path / "v1.json"
    mixed = render(BASE, crlf=True)
    baseline.write_bytes(mixed[:-2] + b"\n")  # 마지막 줄만 LF 단독 -- 섞인 줄바꿈
    current = tmp_path / "cur.json"
    current.write_text(json.dumps(CURRENT), encoding="utf-8")
    assert main(["--current", str(current), "--baseline", str(baseline), "--path", "/b"]) == 0
    assert "정규화" in capsys.readouterr().out
    assert b"\r\n" in baseline.read_bytes() and b"\n" not in baseline.read_bytes().replace(
        b"\r\n", b""
    )
