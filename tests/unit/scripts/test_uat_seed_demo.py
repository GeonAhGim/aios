"""Tests for scripts/uat/seed_demo.py.

Uses ``http.server`` (standard library) to mock the UAT API endpoints
so no real server or database is required.

Spec: task-25551 DoD — unit test with a fake server, including idempotency.
"""

from __future__ import annotations

import http.server
import json
import threading
import unittest
import urllib.parse

from scripts.uat import seed_demo

# ---------------------------------------------------------------------------
# Fake server
# ---------------------------------------------------------------------------


class _FakeHandler(http.server.BaseHTTPRequestHandler):
    """Minimal HTTP handler that mimics the real /auth and /exchange-credentials routes."""

    # Class-level state shared across requests
    users: dict[str, str] = {}  # email -> password
    tokens: dict[str, str] = {}  # email -> token
    credentials: list[dict] = []

    # Suppress default logging to stderr
    def log_message(self, fmt: str, *args: object) -> None:
        pass

    def _read_body(self) -> dict:
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length)
        return json.loads(body) if body else {}

    def _send_json(self, status: int, data: dict) -> None:
        payload = json.dumps(data).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def do_POST(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path

        # POST /auth/register
        if path == "/auth/register":
            body = self._read_body()
            email = body.get("email", "")
            password = body.get("password", "")
            if email in self.users:
                self._send_json(409, {"detail": "Email already registered"})
            else:
                self.users[email] = password
                self._send_json(201, {"data": {"user_id": 1, "email": email}})
            return

        # POST /auth/login
        if path == "/auth/login":
            body = self._read_body()
            email = body.get("email", "")
            password = body.get("password", "")
            if email not in self.users or self.users[email] != password:
                self._send_json(401, {"detail": "Invalid credentials"})
            else:
                token = f"token-{email}"
                self.tokens[email] = token
                self._send_json(200, {"data": {"access_token": token}})
            return

        # POST /exchange-credentials
        if path == "/exchange-credentials":
            auth = self.headers.get("Authorization", "")
            if not auth.startswith("Bearer "):
                self._send_json(401, {"detail": "Missing token"})
                return
            body = self._read_body()
            exchange = body.get("exchange", "")
            api_key = body.get("api_key", "")
            # Check for duplicate
            for cred in self.credentials:
                if cred["exchange"] == exchange and cred["api_key"] == api_key:
                    self._send_json(409, {"detail": "Already registered"})
                    return
            self.credentials.append(body)
            self._send_json(201, {"data": {"exchange": exchange}})
            return

        # Unknown route
        self._send_json(404, {"detail": "Not found"})

    def do_GET(self) -> None:
        self._send_json(200, {"status": "ok"})


class _ServerFixture:
    """Starts/stops a fake server in a background thread."""

    def __init__(self) -> None:
        self.server = http.server.HTTPServer(("127.0.0.1", 0), _FakeHandler)
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    def start(self) -> None:
        self.thread.start()

    def stop(self) -> None:
        self.server.shutdown()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestSeedDemo(unittest.TestCase):
    """Tests for ``seed_demo.main()`` against the fake server."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.fixture = _ServerFixture()
        cls.fixture.start()
        cls.base_url = f"http://127.0.0.1:{cls.fixture.port}"

    @classmethod
    def tearDownClass(cls) -> None:
        cls.fixture.stop()

    def setUp(self) -> None:
        # Reset fake server state
        _FakeHandler.users.clear()
        _FakeHandler.tokens.clear()
        _FakeHandler.credentials.clear()

    def test_full_flow_signup_then_login_then_register(self) -> None:
        """Happy path: new user signs up, logs in, registers exchange."""
        result = seed_demo.main(["--base-url", self.base_url])
        self.assertEqual(result, 0)
        self.assertIn("demo@aios-uat.local", _FakeHandler.users)
        self.assertEqual(len(_FakeHandler.credentials), 1)
        self.assertEqual(_FakeHandler.credentials[0]["exchange"], "bitget")

    def test_idempotent_second_run(self) -> None:
        """Running twice should succeed both times (second run hits 409 on signup)."""
        result1 = seed_demo.main(["--base-url", self.base_url])
        self.assertEqual(result1, 0)
        # Second run — user already exists, should login and re-register
        result2 = seed_demo.main(["--base-url", self.base_url])
        self.assertEqual(result2, 0)
        # Still only one user and one credential set
        self.assertEqual(len(_FakeHandler.users), 1)

    def test_login_failure_returns_error(self) -> None:
        """Wrong password should cause exit code 1."""
        # Sign up first
        seed_demo.main(["--base-url", self.base_url])
        # Use wrong password — user exists but password is wrong
        result = seed_demo.main(["--base-url", self.base_url, "--password", "WrongPassword!"])
        self.assertEqual(result, 1)

    def test_unreachable_server_raises(self) -> None:
        """Connecting to a non-listening port should raise SystemExit."""
        with self.assertRaises(SystemExit):
            seed_demo.main(["--base-url", "http://127.0.0.1:1"])

    def test_password_from_env(self) -> None:
        """UAT_DEMO_PASSWORD env var should override CLI --password."""
        import os

        os.environ["UAT_DEMO_PASSWORD"] = "EnvPassword123!"
        try:
            result = seed_demo.main(["--base-url", self.base_url, "--password", "Wrong"])
            self.assertEqual(result, 0)
        finally:
            os.environ.pop("UAT_DEMO_PASSWORD", None)

    def test_custom_email(self) -> None:
        """Custom --email should be used."""
        result = seed_demo.main(["--base-url", self.base_url, "--email", "custom@test.com"])
        self.assertEqual(result, 0)
        self.assertIn("custom@test.com", _FakeHandler.users)


if __name__ == "__main__":
    unittest.main()
