"""Collect task-9986 package boundary tests during normal directory runs."""

from . import (
    test_failure_injection_redaction_error_prevents_sink_write,
    test_formatter_p95_within_logging_budget,
    test_negative_formatter_rejects_invalid_contract,
    test_red_gate_reproduction_detects_redaction_bypass,
)

__all__ = [
    "test_failure_injection_redaction_error_prevents_sink_write",
    "test_formatter_p95_within_logging_budget",
    "test_negative_formatter_rejects_invalid_contract",
    "test_red_gate_reproduction_detects_redaction_bypass",
]
