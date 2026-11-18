"""scripts/uat/write_probe.py -- safety, operation selection, classification, fake-server run."""

from __future__ import annotations

import json
import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest

from scripts.uat import write_probe as wr


def test_only_loopback_urls_are_allowed() -> None:
    assert wr.is_loopback("http://127.0.0.1:18000")
    assert wr.is_loopback("http://localhost:8000")
    assert not wr.is_loopback("https://api.example.com")
    assert not wr.is_loopback("http://10.0.0.5:18000")


def test_main_refuses_non_loopback_without_any_request() -> None:
    assert wr.main(["--base-url", "https://api.example.com"]) == 2


def test_fill_path_replaces_every_parameter() -> None:
    assert wr.fill_path("/a/{x}/b/{y}") == f"/a/{wr.DUMMY_UUID}/b/{wr.DUMMY_UUID}"
    assert wr.fill_path("/plain") == "/plain"


def test_operations_pick_writes_and_param_gets_but_skip_admin_and_auth() -> None:
    spec = {
        "paths": {
            "/items": {"get": {}, "post": {}},
            "/items/{id}": {"get": {}, "delete": {}},
            "/admin/users": {"post": {}},
            "/auth/login": {"post": {}},
        }
    }
    assert wr.operations(spec) == [
        ("POST", "/items"),
        ("DELETE", "/items/{id}"),
        ("GET", "/items/{id}"),
    ]


@pytest.mark.parametrize(
    ("method", "status", "bucket"),
    [
        ("POST", 422, "needs_input"),
        ("POST", 403, "needs_role"),
        ("POST", 404, "missing"),
        ("POST", 500, "server_error"),
        ("POST", 501, "unimplemented"),
        ("POST", 201, "accepted_empty"),
        ("GET", 200, "wired"),
    ],
)
def test_classify_write_buckets(method: str, status: int, bucket: str) -> None:
    assert wr.classify_write(method, status, "{}")[0] == bucket


def test_stub_marker_in_a_write_success_is_reported_as_unimplemented() -> None:
    bucket, note = wr.classify_write("POST", 200, '{"detail": "not implemented yet"}')
    assert bucket == "unimplemented" and note == "stub marker in body"


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
            paths = {"/ok": {"post": {}}, "/boom": {"post": {}}, "/item/{id}": {"get": {}}}
            self._send(200, {"paths": paths})
        else:
            self._send(404, {"error_code": "NOT_FOUND"})

    def do_POST(self) -> None:  # noqa: N802
        if self.path == "/auth/register":
            self._send(201, {"data": {"access_token": "tok"}})
        elif self.path == "/boom":
            self._send(500, {"error_code": "INTERNAL_ERROR"})
        else:
            self._send(422, {"error_code": "VALIDATION"})


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


def test_run_reports_server_errors_in_the_attention_section(
    server: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(wr, "SLEEP_BETWEEN", 0)
    out = tmp_path / "w.md"
    assert wr.main(["--base-url", server, "--out-md", str(out)]) == 0
    text = out.read_text(encoding="utf-8")
    assert "| POST | `/boom` | 500 |" in text
    assert "| POST | `/ok` |" not in text  # 422 is healthy, not an attention item
    assert (
        "`/item/{id}`" not in text.split("## Operations that accepted")[0].split("attention)")[-1]
    )
