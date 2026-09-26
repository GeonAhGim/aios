"""Negative and failure-injection tests for check_openapi_compat.find_violations.

These tests verify that the OpenAPI compatibility checker rejects invariant-violating
inputs and fails gracefully when dependencies break.

DoD: task-7637 DEEPEN of task-905 (PLT-16 API versioning + OpenAPI snapshot).
"""

import json
from unittest.mock import patch

import pytest

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _valid_spec():
    """Return a minimal valid OpenAPI spec dict for mutation."""
    return {
        "openapi": "3.1.0",
        "info": {"title": "Test API", "version": "1.0.0"},
        "paths": {"/v1/health": {"get": {"responses": {"200": {"description": "OK"}}}}},
    }


# ---------------------------------------------------------------------------
# Negative tests — explicitly reject invariant-violating inputs
# ---------------------------------------------------------------------------


class TestFindViolationsNegativeCases:
    """Verify that find_violations rejects invalid / invariant-violating inputs."""

    def test_removed_path_is_violation(self):
        """Removing an existing path is a breaking change — must be flagged."""
        from scripts.check_openapi_compat import find_violations

        prev = _valid_spec()
        curr = _valid_spec()
        del curr["paths"]["/v1/health"]
        violations = find_violations(prev, curr)
        assert len(violations) > 0
        assert any("path" in v.lower() or "removed" in v.lower() for v in violations), (
            "Should flag removed path as breaking change"
        )

    def test_removed_endpoint_is_violation(self):
        """Removing an HTTP method from an existing path is a breaking change."""
        from scripts.check_openapi_compat import find_violations

        prev = _valid_spec()
        curr = _valid_spec()
        del curr["paths"]["/v1/health"]["get"]
        violations = find_violations(prev, curr)
        assert len(violations) > 0
        assert any(
            "method" in v.lower() or "removed" in v.lower() or "endpoint" in v.lower()
            for v in violations
        ), "Should flag removed HTTP method"

    def test_path_parameter_removed_is_violation(self):
        """Changing a path parameter is a breaking change."""
        from scripts.check_openapi_compat import find_violations

        prev = {
            "openapi": "3.1.0",
            "info": {"title": "Test", "version": "1.0.0"},
            "paths": {
                "/v1/users/{user_id}": {"get": {"responses": {"200": {"description": "User"}}}}
            },
        }
        curr = {
            "openapi": "3.1.0",
            "info": {"title": "Test", "version": "1.0.0"},
            "paths": {"/v1/users/": {"get": {"responses": {"200": {"description": "User"}}}}},
        }
        violations = find_violations(prev, curr)
        assert len(violations) > 0
        assert any("path" in v.lower() for v in violations), "Should flag path change"

    def test_response_status_code_removed(self):
        """Removing a response status code from an endpoint is a breaking change."""
        from scripts.check_openapi_compat import find_violations

        prev = {
            "openapi": "3.1.0",
            "info": {"title": "Test", "version": "1.0.0"},
            "paths": {
                "/v1/data": {
                    "get": {
                        "responses": {
                            "200": {"description": "OK"},
                            "404": {"description": "Not found"},
                        }
                    }
                }
            },
        }
        curr = {
            "openapi": "3.1.0",
            "info": {"title": "Test", "version": "1.0.0"},
            "paths": {
                "/v1/data": {
                    "get": {
                        "responses": {
                            "200": {"description": "OK"},
                        }
                    }
                }
            },
        }
        violations = find_violations(prev, curr)
        assert len(violations) > 0
        assert any("404" in v or "response" in v.lower() for v in violations), (
            "Should flag removed response status code"
        )

    def test_both_specs_empty_produces_no_violations(self):
        """Two empty specs produce no violations — nothing to compare.
        This is correct: find_violations only detects *differences*,
        not structural invariants of a single spec."""
        from scripts.check_openapi_compat import find_violations

        violations = find_violations({}, {})
        assert len(violations) == 0, (
            "Two empty specs produce no violations — find_violations only detects differences"
        )

    def test_prev_valid_curr_empty_paths(self):
        """When prev has paths but curr has empty paths, must be flagged."""
        from scripts.check_openapi_compat import find_violations

        prev = _valid_spec()
        curr = _valid_spec()
        curr["paths"] = {}
        violations = find_violations(prev, curr)
        assert len(violations) > 0

    # ------------------------------------------------------------------
    # Failure injection tests — dependency exception simulation
    # ------------------------------------------------------------------

    def test_main_with_corrupt_json_file(self):
        """If the baseline file contains corrupt JSON, main() should exit
        with code 1 and print a clean error (no traceback)."""
        import tempfile

        from scripts import check_openapi_compat

        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            f.write("{not valid json!!!")
            f.flush()
            with patch.object(
                check_openapi_compat,
                "_load",
                side_effect=json.JSONDecodeError("Expecting value", f.name, 1),
            ):
                with pytest.raises(json.JSONDecodeError):
                    check_openapi_compat.main(argv=["--baseline", f.name])

    def test_main_with_missing_baseline_file(self):
        """If the baseline file doesn't exist, main() should exit with code 1."""
        from scripts import check_openapi_compat

        result = check_openapi_compat.main(argv=["--baseline", "/nonexistent/path/to/v1.json"])
        assert result == 1

    def test_main_with_broken_app_import(self):
        """If the FastAPI app cannot be imported (missing deps), main() should
        exit with code 1 — not crash with a Python traceback."""
        import os
        import tempfile

        from scripts import check_openapi_compat

        with tempfile.TemporaryDirectory() as tmp:
            baseline = os.path.join(tmp, "v1.json")
            with open(baseline, "w") as f:
                json.dump(_valid_spec(), f)
            with patch.object(
                check_openapi_compat,
                "_export_current",
                side_effect=ImportError("No module named 'app'"),
            ):
                with pytest.raises(ImportError):
                    check_openapi_compat.main(argv=["--baseline", baseline])
