"""Normal pytest discovery for the directly executable fixture contract tests."""

from .conftest import (
    test_failure_injection_consumer_closes_pool as test_failure_injection_consumer_closes_pool,
)
from .conftest import (
    test_failure_injection_pool_close as test_failure_injection_pool_close,
)
from .conftest import (
    test_failure_injection_pool_creation as test_failure_injection_pool_creation,
)
from .conftest import (
    test_negative_invalid_database_url as test_negative_invalid_database_url,
)
from .conftest import (
    test_negative_missing_database_url as test_negative_missing_database_url,
)
from .conftest import (
    test_pool_normalizes_dsn_and_closes as test_pool_normalizes_dsn_and_closes,
)
