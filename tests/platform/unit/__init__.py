"""Tests for break_glass module.

Re-exports test classes from test_restored_deepen so that
``pytest tests/platform/unit/__init__.py`` collects them (DoD for task-24937 /
task-9963 / task-6704).
"""

from tests.platform.unit.test_restored_deepen import (
    TestFailureInjection,
    TestNegativeApproveGrant,
    TestNegativeConsume,
    TestNegativeMfaStepUpFresh,
    TestNegativeRequestGrant,
    TestPerformance,
)

__all__ = [
    "TestNegativeApproveGrant",
    "TestNegativeConsume",
    "TestNegativeMfaStepUpFresh",
    "TestNegativeRequestGrant",
    "TestPerformance",
    "TestFailureInjection",
]
