"""Collect auth fixture regression checks during ordinary directory test runs."""

from tests.integration.auth.conftest import (  # noqa: F401
    test_dsn_conversion_preserves_connection_parameters,
    test_failure_injection_pool_close_error_propagates,
    test_failure_injection_pool_creation_error_propagates,
    test_negative_invalid_database_url_rejected_before_connection,
    test_negative_missing_database_url_rejected_before_connection,
    test_pool_cleanup_including_failure_injection,
)
