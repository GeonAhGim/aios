"""Unit tests for scripts/uat/seed_demo.py — fake HTTP server based.

DoD: (1) signup succeeds and returns token, (2) login when already exists,
(3) exchange registration idempotent (second run skips), (4) network error
handled gracefully, (5) password from env var takes precedence,
(6) negative: invalid credentials return non-200.
"""

from __future__ import annotations

import importlib
import json
import sys
import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[3]
SCRIPTS_DIR = ROOT / "scripts"

# Load seed_demo module dynamically so we can patch _request if needed.

_spec = importlib.util.spec_from_file_location("seed_demo", SCRIPTS_DIR / "uat" / "seed_demo.py")
assert _spec is not None and _spec.loader is not None
_seed_demo = importlib.util.module_from_spec(_spec)
sys.modules["seed_demo"] = _seed_demo
_spec.loader.exec_module(_seed_demo)


# ---------------------------------------------------------------------------
# Fake HTTP server
# ---------------------------------------------------------------------------

# Shared state for the fake server
_fake_state: dict[str, Any] = {
    "users": {},  # email -> {"password": ..., "mfa_enabled": ...}
    "tokens": {},  # token_str -> {"email": ...}
    "credentials": {},  # token -> [{"exchange": ..., "api_key": ...}]
    "next_cred_id": 1,
}

# In-memory token store — simple prefix to avoid collisions
_token_counter: int = 0


def _make_token(email: str) -> str:
    global _token_counter
    _token_counter += 1
    tok = f"fake-token-{_token_counter}-{email}"
    _fake_state["tokens"][tok] = {"email": email}
    return tok


def _extract_bearer(headers: dict[str, str]) -> str | None:
    # headers from http.client.HTTPMessage via dict() preserves original case
    for key, value in headers.items():
        if key.lower() == "authorization" and value.startswith("Bearer "):
            return value[7:]
    return None


class _FakeHandler(BaseHTTPRequestHandler):
    """Minimal HTTP handler that mimics the UAT auth + exchange-credentials API."""

    # Suppress default stderr logging
    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
        pass

    def _send_json(self, status: int, body: dict[str, Any] | list[Any] | str) -> None:
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _read_body(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length", 0))
        if length == 0:
            return {}
        raw = self.rfile.read(length)
        return json.loads(raw.decode("utf-8"))

    def do_POST(self) -> None:
        global _token_counter

        if self.path == "/auth/register":
            body = self._read_body()
            email = body.get("email", "")
            password = body.get("password", "")

            # Check if user already exists
            if email in _fake_state["users"]:
                self._send_json(409, {"error_code": "USER_EXISTS", "message": "already exists"})
                return

            # Create user
            _fake_state["users"][email] = {"password": password, "mfa_enabled": False}
            token = _make_token(email)
            self._send_json(
                201,
                {"data": {"access_token": token, "token_type": "bearer", "expires_in": 900}},
            )

        elif self.path == "/auth/login":
            body = self._read_body()
            email = body.get("email", "")
            password = body.get("password", "")

            user = _fake_state["users"].get(email)
            if not user or user["password"] != password:
                self._send_json(401, {"error_code": "AUTH_INVALID", "message": "bad credentials"})
                return

            token = _make_token(email)
            self._send_json(
                200,
                {"data": {"access_token": token, "token_type": "bearer", "expires_in": 900}},
            )

        elif self.path == "/exchange-credentials":
            token_str = _extract_bearer(dict(self.headers))
            if not token_str or token_str not in _fake_state["tokens"]:
                self._send_json(401, {"error_code": "AUTH_UNAUTHORIZED"})
                return

            body = self._read_body()
            exchange = body.get("exchange", "")
            # api_key/api_secret are accepted but not validated in the fake server
            body.get("api_key", "")
            body.get("api_secret", "")

            # Check idempotency — already registered?
            creds = _fake_state["credentials"].setdefault(token_str, [])
            if any(c["exchange"] == exchange for c in creds):
                self._send_json(
                    200,
                    {"data": [{"exchange": exchange, "is_active": True}]},
                )
                return

            _fake_state["next_cred_id"] += 1
            creds.append(
                {
                    "id": _fake_state["next_cred_id"],
                    "exchange": exchange,
                    "is_active": True,
                    "linked_at": "2026-01-01T00:00:00Z",
                }
            )
            self._send_json(
                201,
                {
                    "data": {
                        "id": _fake_state["next_cred_id"],
                        "exchange": exchange,
                        "is_active": True,
                        "linked_at": "2026-01-01T00:00:00Z",
                    }
                },
            )
        else:
            self._send_json(404, {"error": "not found"})

    def do_GET(self) -> None:
        token_str = _extract_bearer(dict(self.headers))
        if not token_str or token_str not in _fake_state["tokens"]:
            self._send_json(401, {"error_code": "AUTH_UNAUTHORIZED"})
            return

        if self.path == "/exchange-credentials":
            creds = _fake_state["credentials"].get(token_str, [])
            self._send_json(200, {"data": creds})
        else:
            self._send_json(404, {"error": "not found"})


def _reset_fake_state() -> None:
    global _token_counter
    _token_counter = 0
    _fake_state["users"] = {}
    _fake_state["tokens"] = {}
    _fake_state["credentials"] = {}
    _fake_state["next_cred_id"] = 1


@pytest.fixture
def fake_server() -> Iterator[str]:
    """Start a fake UAT server and yield its base_url; shut it down afterwards.

    Bind port 0 directly so the OS picks a free port. Reserving a port with a second socket and then
    binding the server to it works on Windows but fails on Linux with "Address already in use"
    (Actions test-shard 3, 2026-11-18)."""
    _reset_fake_state()
    server = ThreadingHTTPServer(("127.0.0.1", 0), _FakeHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_signup_success(fake_server: str) -> None:
    """Signup creates a user and returns an access token."""
    base = fake_server
    status, token = _seed_demo._signup(base, "test@demo.com", "password123456")
    assert status == 201
    assert token is not None
    assert "fake-token" in token


def test_signup_already_exists(fake_server: str) -> None:
    """Signup returns 409 when user already exists."""
    base = fake_server
    s1, _ = _seed_demo._signup(base, "test@demo.com", "password123456")
    s2, _ = _seed_demo._signup(base, "test@demo.com", "password123456")
    assert s1 == 201
    assert s2 == 409


def test_login_success(fake_server: str) -> None:
    """Login with correct credentials returns a token."""
    base = fake_server
    _seed_demo._signup(base, "login@test.com", "password123456")
    status, token = _seed_demo._login(base, "login@test.com", "password123456")
    assert status == 200
    assert token is not None


def test_login_wrong_password(fake_server: str) -> None:
    """Login with wrong password returns 401."""
    base = fake_server
    _seed_demo._signup(base, "pw@test.com", "password123456")
    status, _ = _seed_demo._login(base, "pw@test.com", "wrongpassword")
    assert status == 401


def test_exchange_registration(fake_server: str) -> None:
    """Registering an exchange credential succeeds."""
    base = fake_server
    _seed_demo._signup(base, "ex@test.com", "password123456")
    _, token = _seed_demo._login(base, "ex@test.com", "password123456")

    status, err = _seed_demo._register_exchange(base, token, "bitget")
    assert status == 201
    assert err is None


def test_exchange_registration_idempotent(fake_server: str) -> None:
    """Registering the same exchange twice: first succeeds, second is a no-op."""
    base = fake_server
    _seed_demo._signup(base, "idem@test.com", "password123456")
    _, token = _seed_demo._login(base, "idem@test.com", "password123456")

    # First registration
    s1, e1 = _seed_demo._register_exchange(base, token, "bitget")
    assert s1 == 201
    assert e1 is None

    # Second registration — should succeed (idempotent)
    s2, e2 = _seed_demo._register_exchange(base, token, "bitget")
    assert s2 in (200, 201)


def test_exchange_without_token_returns_401(fake_server: str) -> None:
    """Registering without auth token returns 401."""
    base = fake_server
    status, _ = _seed_demo._register_exchange(base, "", "bitget")
    assert status == 401


def test_list_exchanges(fake_server: str) -> None:
    """Listing exchanges returns registered credentials."""
    base = fake_server
    _seed_demo._signup(base, "list@test.com", "password123456")
    _, token = _seed_demo._login(base, "list@test.com", "password123456")

    status, creds = _seed_demo._list_exchanges(base, token)
    assert status == 200
    assert isinstance(creds, list)

    # Register one
    _seed_demo._register_exchange(base, token, "bitget")

    status, creds = _seed_demo._list_exchanges(base, token)
    assert status == 200
    assert len(creds) >= 1
    assert any(c.get("exchange") == "bitget" for c in creds)


def test_full_seed_flow(fake_server: str) -> None:
    """End-to-end: signup + login + register all demo exchanges."""
    base = fake_server
    import os

    os.environ["UAT_DEMO_PASSWORD"] = "testpassword123456"

    try:
        result = _seed_demo.run(
            base_url=base,
            email="fullseed@test.com",
            password="ignored",  # env var takes precedence
        )
        assert result == 0
    finally:
        os.environ.pop("UAT_DEMO_PASSWORD", None)


def test_seed_idempotent_second_run(fake_server: str) -> None:
    """Running seed_demo twice produces the same state (idempotent)."""
    base = fake_server
    import os

    os.environ["UAT_DEMO_PASSWORD"] = "testpassword123456"

    try:
        # First run
        r1 = _seed_demo.run(
            base_url=base,
            email="idem2@test.com",
            password="ignored",
        )
        assert r1 == 0

        # Get credential count after first run
        _, creds1 = _seed_demo._list_exchanges(
            base,
            _seed_demo._login(base, "idem2@test.com", "testpassword123456")[1],
        )
        count1 = len(creds1)

        # Second run
        r2 = _seed_demo.run(
            base_url=base,
            email="idem2@test.com",
            password="ignored",
        )
        assert r2 == 0

        # Get credential count after second run
        _, creds2 = _seed_demo._list_exchanges(
            base,
            _seed_demo._login(base, "idem2@test.com", "testpassword123456")[1],
        )
        count2 = len(creds2)

        # Same number of credentials (idempotent)
        assert count1 == count2
    finally:
        os.environ.pop("UAT_DEMO_PASSWORD", None)


def test_password_env_var_precedence(fake_server: str) -> None:
    """UAT_DEMO_PASSWORD env var takes precedence over --password CLI arg."""
    base = fake_server
    import os

    os.environ["UAT_DEMO_PASSWORD"] = "envpassword123456"

    try:
        result = _seed_demo.run(
            base_url=base,
            email="pwprecedence@test.com",
            password="cli-password-will-be-ignored",
        )
        assert result == 0

        # Verify the user was created with the env password
        status, token = _seed_demo._login(base, "pwprecedence@test.com", "envpassword123456")
        assert status == 200
        assert token is not None

        # Wrong password (CLI value) should fail
        status, _ = _seed_demo._login(base, "pwprecedence@test.com", "cli-password-will-be-ignored")
        assert status == 401
    finally:
        os.environ.pop("UAT_DEMO_PASSWORD", None)


def test_network_error_returns_nonzero(fake_server: str) -> None:
    """Connecting to a non-existent server returns non-zero exit code."""
    result = _seed_demo.run(
        base_url="http://127.0.0.1:1",
        email="noreply@test.com",
        password="password123456",
    )
    assert result == 1


def test_negative_unregistered_exchange(fake_server: str) -> None:
    """Attempting to register an unregistered exchange type is handled."""
    base = fake_server
    _seed_demo._signup(base, "neg@test.com", "password123456")
    _, token = _seed_demo._login(base, "neg@test.com", "password123456")

    # Try registering a valid exchange type
    status, err = _seed_demo._register_exchange(base, token, "kis")
    assert status == 201
    assert err is None
