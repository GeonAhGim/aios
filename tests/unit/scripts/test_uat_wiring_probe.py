"""scripts/uat/wiring_probe.py -- classification, shape redaction, report, and a fake-server run."""

from __future__ import annotations

import importlib.util
import json
import sys
import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

_PATH = Path(__file__).resolve().parents[3] / "scripts" / "uat" / "wiring_probe.py"


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("wiring_probe_under_test", _PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module  # dataclasses resolve annotations through sys.modules
    spec.loader.exec_module(module)
    return module


probe = _load()


@pytest.mark.parametrize(
    ("status", "body", "bucket"),
    [
        (200, '{"data": []}', "wired"),
        (200, '{"message": "Not Implemented yet"}', "unimplemented"),
        (501, "", "unimplemented"),
        (400, "", "needs_input"),
        (422, "", "needs_input"),
        (401, "", "needs_role"),
        (403, "", "needs_role"),
        (404, "", "missing"),
        (405, "", "missing"),
        (429, "", "rate_limited"),
        (503, "", "server_error"),
        (0, "", "unreachable"),
    ],
)
def test_classify_buckets(status: int, body: str, bucket: str) -> None:
    assert probe.classify(status, body)[0] == bucket


def test_stub_marker_in_success_body_is_flagged() -> None:
    bucket, stub = probe.classify(200, '{"note": "this is a STUB response"}')
    assert (bucket, stub) == ("unimplemented", True)


def test_shape_never_leaks_values() -> None:
    body = json.dumps({"data": {"email": "a@b.c", "token": "SECRET-VALUE", "n": 1}})
    shape = probe.shape_of(body)
    assert "SECRET-VALUE" not in shape and "a@b.c" not in shape
    assert shape.startswith("data{")


def test_shape_handles_lists_and_garbage() -> None:
    assert probe.shape_of('{"data": [1, 2, 3]}') == "data[list:3]"
    assert probe.shape_of("[1,2]") == "list:2"
    assert probe.shape_of("not json") == "non-json"
    assert probe.shape_of("") == "empty"


def test_parameter_free_gets_skips_path_params_and_writes() -> None:
    spec = {
        "paths": {
            "/a": {"get": {}},
            "/b/{id}": {"get": {}},
            "/c": {"post": {}},
            "/d": {"get": {}, "post": {}},
        }
    }
    assert probe.parameter_free_gets(spec) == ["/a", "/d"]


def test_render_markdown_lists_problems_and_counts() -> None:
    results = [
        probe.ProbeResult("/ok", 200, "wired", 5, "data[list:0]", False),
        probe.ProbeResult("/gone", 404, "missing", 7, "{error_code}", False),
    ]
    md = probe.render_markdown(results, 3, {"Static": ["x"]})
    assert "| wired | 2xx, real answer | 1 |" in md
    assert "| `/gone` | 404 | missing |" in md
    assert "skipped 3 with path params" in md
    assert "- `x`" in md


class _Handler(BaseHTTPRequestHandler):
    def log_message(self, *args: Any) -> None:
        pass

    def _send(self, status: int, payload: Any) -> None:
        raw = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self) -> None:  # noqa: N802
        if self.path == "/openapi.json":
            self._send(
                200, {"paths": {"/live": {"get": {}}, "/gone": {"get": {}}, "/x/{i}": {"get": {}}}}
            )
        elif self.path == "/live":
            self._send(200, {"data": [1]})
        else:
            self._send(404, {"error_code": "NOT_FOUND"})

    def do_POST(self) -> None:  # noqa: N802
        self._send(201, {"data": {"access_token": "tok"}})


@pytest.fixture
def server() -> Iterator[str]:
    srv = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{srv.server_address[1]}"
    finally:
        srv.shutdown()
        srv.server_close()
        thread.join(timeout=5)


def test_run_against_fake_server_classifies_live_and_missing(server: str, tmp_path: Path) -> None:
    out_json = tmp_path / "w.json"
    out_md = tmp_path / "w.md"
    rc = probe.main(["--base-url", server, "--out-json", str(out_json), "--out-md", str(out_md)])
    assert rc == 0
    rows = {r["path"]: r["bucket"] for r in json.loads(out_json.read_text(encoding="utf-8"))}
    assert rows == {"/live": "wired", "/gone": "missing"}
    assert "skipped 1 with path params" in out_md.read_text(encoding="utf-8")


def test_unreachable_server_returns_error_code() -> None:
    assert probe.main(["--base-url", "http://127.0.0.1:1"]) == 2
