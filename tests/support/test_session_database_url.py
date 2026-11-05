"""tests/support/test_session_database_url.py — pure-unit tests for session_database_url.

Spec: docs/specs/L4_platform_observability_tenancy_api_v1.0.md §2.4/§9 PLT-36.
DoD: negative test ≥3, failure-injection ≥1, perf assertion, gate-red repro.

Split from test_db.py so these I/O-free unit tests run in parallel without
being blocked by the slow real-DB clone test
(test_ensure_worker_database_clone_meets_latency_budget).
"""

from __future__ import annotations

import pytest

from tests.support.db import session_database_url

# ── Negative tests: invalid DB names rejected ─────────────────────────


def test_session_database_url_rejects_special_chars_in_db_name() -> None:
    """DB 이름에 특수문자(-)가 있으면 ValueError로 거부한다."""
    with pytest.raises(ValueError, match="예상치 못한 템플릿 DB 이름"):
        session_database_url("postgresql://user:pass@localhost/my-db", "gw0")


def test_session_database_url_rejects_uppercase_db_name() -> None:
    """DB 이름에 대문자가混入되면 ValueError로 거부한다."""
    with pytest.raises(ValueError, match="예상치 못한 템플릿 DB 이름"):
        session_database_url("postgresql://user:pass@localhost/MyDB", "gw0")


def test_session_database_url_rejects_db_name_exceeding_40_chars() -> None:
    """DB 이름이 40자를 초과하면 ValueError로 거부한다."""
    long_name = "a" * 41
    with pytest.raises(ValueError, match="예상치 못한 템플릿 DB 이름"):
        session_database_url(f"postgresql://user:pass@localhost/{long_name}", "gw0")


def test_session_database_url_rejects_long_worker_db_name() -> None:
    """템플릿+워커 접미사가 40자를 초과하면 ValueError로 거부한다."""
    # template 36 chars + "_gw0" = 40 chars exactly — OK
    # template 37 chars + "_gw0" = 41 chars — should fail
    long_template = "a" * 37
    with pytest.raises(ValueError, match="워커 DB 이름이 규칙"):
        session_database_url(f"postgresql://user:pass@localhost/{long_template}", "gw0")


def test_session_database_url_master_returns_template_unchanged() -> None:
    """worker_id == "master"면 template URL을 그대로 반환한다."""
    url = "postgresql://user:pass@localhost:5432/aios_test"
    assert session_database_url(url, "master") == url


# ── Boundary: valid inputs pass through ──────────────────────────────


def test_session_database_url_valid_name_concats_worker_suffix() -> None:
    """유효한 DB 이름에 워커 접미사가 올바르게 붙는다."""
    result = session_database_url("postgresql://user:pass@localhost:5432/aios_test", "gw0")
    assert "aios_test_gw0" in result
    assert result != "postgresql://user:pass@localhost:5432/aios_test"
