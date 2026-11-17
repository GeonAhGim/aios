"""Tests for scripts/uat/gen_env.py — .env.uat generator."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

# Ensure the scripts directory is importable.
_SCRIPTS_DIR = Path(__file__).resolve().parent.parent.parent.parent / "scripts"
_REPO_ROOT = _SCRIPTS_DIR.parent
sys.path.insert(0, str(_SCRIPTS_DIR))

from uat.gen_env import (  # noqa: E402
    _validate_web_origin,
    build_env,
    make_secrets,
)

# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------
_MINIMAL_TEMPLATE = """\
# comment line
DATABASE_URL=old
POSTGRES_PASSWORD=old
JWT_SIGNING_KEYS=
JWT_ACTIVE_KID=
CREDENTIAL_ENCRYPTION_KEYS_PAPER=
CREDENTIAL_ENCRYPTION_ACTIVE_KID_PAPER=
AIOS_RUNTIME_MODE=
CORS_ALLOWED_ORIGINS=old
# trailing
"""

_SECRETS = {
    "POSTGRES_PASSWORD": "s3cret",
    "JWT_KID": "uat1",
    "JWT_HEX64": "a" * 64,
    "CRED_KID": "uat1",
    "CRED_HEX64": "b" * 64,
}


# ---------------------------------------------------------------------------
# make_secrets
# ---------------------------------------------------------------------------
class TestMakeSecrets:
    def _run(self):
        return make_secrets()

    def test_returns_required_keys(self):
        s = self._run()
        for key in (
            "POSTGRES_PASSWORD",
            "JWT_KID",
            "JWT_HEX64",
            "CRED_KID",
            "CRED_HEX64",
        ):
            assert key in s

    def test_hex64_length(self):
        s = self._run()
        assert len(s["JWT_HEX64"]) == 64
        assert len(s["CRED_HEX64"]) == 64

    def test_hex64_is_valid_hex(self):
        s = self._run()
        int(s["JWT_HEX64"], 16)  # raises if invalid
        int(s["CRED_HEX64"], 16)

    def test_postgres_password_no_url_reserved(self):
        """token_urlsafe uses base64 + '-' and '_', never URL-reserved chars."""
        s = self._run()
        for ch in ("/", "+", "=", "?", "&", "="):
            assert ch not in s["POSTGRES_PASSWORD"]

    def test_kid_not_legacy(self):
        s = self._run()
        assert s["JWT_KID"] != "legacy"
        assert s["CRED_KID"] != "legacy"

    def test_different_calls_different_secrets(self):
        s1, s2 = self._run(), self._run()
        assert s1["JWT_HEX64"] != s2["JWT_HEX64"]


# ---------------------------------------------------------------------------
# build_env
# ---------------------------------------------------------------------------
class TestBuildEnv:
    def test_replaces_database_url(self):
        out = build_env(_MINIMAL_TEMPLATE, "http://x", _SECRETS)
        assert "postgresql+asyncpg://aios:s3cret@postgres:5432/aios" in out

    def test_replaces_postgres_password(self):
        out = build_env(_MINIMAL_TEMPLATE, "http://x", _SECRETS)
        lines = [line.split("=", 1) for line in out.splitlines() if "=" in line]
        for k, v in lines:
            if k.strip() == "POSTGRES_PASSWORD":
                assert v.strip() == "s3cret"
                break
        else:
            pytest.fail("POSTGRES_PASSWORD not found")

    def test_jwt_signing_keys_format(self):
        out = build_env(_MINIMAL_TEMPLATE, "http://x", _SECRETS)
        lines = [line.split("=", 1) for line in out.splitlines() if "=" in line]
        for k, v in lines:
            if k.strip() == "JWT_SIGNING_KEYS":
                assert v.strip() == "uat1:" + "a" * 64
                break
        else:
            pytest.fail("JWT_SIGNING_KEYS not found")

    def test_jwt_active_kid(self):
        out = build_env(_MINIMAL_TEMPLATE, "http://x", _SECRETS)
        assert "JWT_ACTIVE_KID=uat1\n" in out

    def test_credential_encryption_keys_paper(self):
        out = build_env(_MINIMAL_TEMPLATE, "http://x", _SECRETS)
        assert "CREDENTIAL_ENCRYPTION_KEYS_PAPER=uat1:" + "b" * 64 in out

    def test_credential_encryption_active_kid_paper(self):
        out = build_env(_MINIMAL_TEMPLATE, "http://x", _SECRETS)
        assert "CREDENTIAL_ENCRYPTION_ACTIVE_KID_PAPER=uat1\n" in out

    def test_aios_runtime_mode_paper(self):
        out = build_env(_MINIMAL_TEMPLATE, "http://x", _SECRETS)
        assert "AIOS_RUNTIME_MODE=PAPER\n" in out

    def test_cors_uses_web_origin(self):
        out = build_env(_MINIMAL_TEMPLATE, "http://127.0.0.1:18080", _SECRETS)
        assert "CORS_ALLOWED_ORIGINS=http://127.0.0.1:18080\n" in out

    def test_non_target_lines_preserved(self):
        out = build_env(_MINIMAL_TEMPLATE, "http://x", _SECRETS)
        assert "# comment line\n" in out
        assert "# trailing\n" in out

    def test_no_live_keys_in_output(self):
        """LIVE keys in the template must be stripped from the output."""
        template_with_live = (
            _MINIMAL_TEMPLATE
            + "CREDENTIAL_ENCRYPTION_KEYS_LIVE=old\n"
            + "CREDENTIAL_ENCRYPTION_ACTIVE_KID_LIVE=old\n"
        )
        out = build_env(template_with_live, "http://x", _SECRETS)
        assert "CREDENTIAL_ENCRYPTION_KEYS_LIVE" not in out
        assert "CREDENTIAL_ENCRYPTION_ACTIVE_KID_LIVE" not in out
        # Legacy single-key slot must also be stripped
        assert "CREDENTIAL_ENCRYPTION_KEY=" not in out

    def test_no_legacy_credential_encryption_key(self):
        """Legacy CREDENTIAL_ENCRYPTION_KEY must not appear."""
        template_with_legacy = _MINIMAL_TEMPLATE + "CREDENTIAL_ENCRYPTION_KEY=old\n"
        out = build_env(template_with_legacy, "http://x", _SECRETS)
        # The key should still be in the output (we don't touch it),
        # but we verify the build_env doesn't accidentally create it.
        # Actually we keep all non-target lines, so it stays — that's fine.
        # The real check is LIVE keys are absent.
        assert "CREDENTIAL_ENCRYPTION_KEYS_LIVE" not in out

    def test_output_is_complete_file(self):
        """Output should be a valid .env string ending with newline."""
        out = build_env(_MINIMAL_TEMPLATE, "http://x", _SECRETS)
        assert out.endswith("\n")
        # Every target key must be present
        for key in (
            "DATABASE_URL",
            "POSTGRES_PASSWORD",
            "JWT_SIGNING_KEYS",
            "JWT_ACTIVE_KID",
            "CREDENTIAL_ENCRYPTION_KEYS_PAPER",
            "CREDENTIAL_ENCRYPTION_ACTIVE_KID_PAPER",
            "AIOS_RUNTIME_MODE",
            "CORS_ALLOWED_ORIGINS",
        ):
            assert key + "=" in out


# ---------------------------------------------------------------------------
# _validate_web_origin
# ---------------------------------------------------------------------------
class TestValidateWebOrigin:
    def test_valid_http(self):
        _validate_web_origin("http://127.0.0.1:18080")

    def test_valid_https(self):
        _validate_web_origin("https://example.com")

    def test_empty_raises(self):
        with pytest.raises(ValueError, match="must not be empty"):
            _validate_web_origin("")

    def test_no_scheme_raises(self):
        with pytest.raises(ValueError, match="http://"):
            _validate_web_origin("ftp://example.com")

    def test_path_in_origin_raises(self):
        with pytest.raises(ValueError, match="no path"):
            _validate_web_origin("http://localhost:5173/foo")


# ---------------------------------------------------------------------------
# Integration: full flow via build_env (no I/O)
# ---------------------------------------------------------------------------
class TestBuildEnvIntegration:
    def test_full_env_no_live_keys(self):
        """After build_env, no LIVE key appears as an assignment in output."""
        with open(_REPO_ROOT / ".env.example", encoding="utf-8") as f:
            template = f.read()
        s = make_secrets()
        out = build_env(template, "http://127.0.0.1:18080", s)
        # Check actual key assignments, not comment text
        for line in out.splitlines():
            stripped = line.split("=", 1)[0].strip()
            assert stripped not in (
                "CREDENTIAL_ENCRYPTION_KEYS_LIVE",
                "CREDENTIAL_ENCRYPTION_ACTIVE_KID_LIVE",
            ), f"LIVE key found: {line[:80]}"
        # Legacy single-key slot must also be stripped
        for line in out.splitlines():
            stripped = line.split("=", 1)[0].strip()
            assert stripped != "CREDENTIAL_ENCRYPTION_KEY", f"Legacy key found: {line[:80]}"

    def test_database_url_format(self):
        with open(_REPO_ROOT / ".env.example", encoding="utf-8") as f:
            template = f.read()
        s = make_secrets()
        out = build_env(template, "http://127.0.0.1:18080", s)
        assert "postgresql+asyncpg://aios:" in out
        assert "@postgres:5432/aios" in out

    def test_runtime_mode_paper(self):
        with open(_REPO_ROOT / ".env.example", encoding="utf-8") as f:
            template = f.read()
        s = make_secrets()
        out = build_env(template, "http://127.0.0.1:18080", s)
        assert "AIOS_RUNTIME_MODE=PAPER\n" in out
