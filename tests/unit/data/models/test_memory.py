import time
from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from src.data.models.base import ProvenanceStatus
from src.data.models.memory import MemoryEntry, MemoryType


def _valid_kwargs(**overrides):
    kwargs = dict(
        memory_type=MemoryType.DECISION,
        content={"action": "buy"},
        source_agent="strategy-agent",
    )
    # Default timestamps for override-friendly base
    base_created = datetime(2026, 9, 30, 12, 0, 0, tzinfo=timezone.utc)
    kwargs["created_at"] = base_created
    kwargs.update(overrides)
    return kwargs


def test_memory_entry_defaults():
    entry = MemoryEntry(**_valid_kwargs())
    assert entry.status == ProvenanceStatus.UNVERIFIED
    assert entry.confidence == 0.5
    assert entry.verified_at is None


def test_memory_entry_confidence_upper_bound_raises():
    with pytest.raises(ValidationError):
        MemoryEntry(**_valid_kwargs(confidence=1.5))


def test_memory_entry_confidence_lower_bound_raises():
    with pytest.raises(ValidationError):
        MemoryEntry(**_valid_kwargs(confidence=-0.1))


def test_memory_entry_invalid_memory_type_raises():
    with pytest.raises(ValidationError):
        MemoryEntry(**_valid_kwargs(memory_type="NOT_A_REAL_TYPE"))


def test_memory_entry_missing_required_field_raises():
    kwargs = _valid_kwargs()
    del kwargs["source_agent"]
    with pytest.raises(ValidationError):
        MemoryEntry(**kwargs)


def test_memory_entry_content_wrong_type_raises():
    with pytest.raises(ValidationError):
        MemoryEntry(**_valid_kwargs(content="not-a-dict"))


def test_memory_entry_dependency_failure_propagates_fail_closed(monkeypatch):
    def boom(*args, **kwargs):
        raise RuntimeError("dependency exploded")

    monkeypatch.setattr(MemoryEntry, "__init__", boom)
    with pytest.raises(RuntimeError):
        MemoryEntry(**_valid_kwargs())


def test_memory_verified_at_before_created_at_raises():
    """negative: verified_at cannot precede created_at (provenance ordering invariant)."""
    created = datetime(2026, 9, 30, 12, 0, 0, tzinfo=timezone.utc)
    verified = datetime(2026, 9, 30, 11, 59, 59, tzinfo=timezone.utc)  # 1s earlier
    with pytest.raises(ValidationError):
        MemoryEntry(
            **_valid_kwargs(created_at=created, verified_at=verified),
        )


def test_memory_verified_by_requires_verified_status():
    """negative: setting verified_by without VERIFIED status is rejected."""
    with pytest.raises(ValidationError):
        MemoryEntry(
            **_valid_kwargs(verified_by="auditor-agent", status=ProvenanceStatus.UNVERIFIED),
        )


def test_memory_uuid4_failure_propagates(monkeypatch):
    """failure-injection: uuid4() exception propagates instead of being swallowed.

    Pydantic v2's compiled validator captures default_factory at class-definition
    time, so we can't patch it after import. Instead we run a subprocess with
    uuid4 patched before the model is imported.
    """

    import subprocess
    import sys

    code = """
import uuid
uuid.uuid4 = lambda *a, **k: (_ for _ in ()).throw(OSError("UUID generation service unavailable"))
from src.data.models.memory import MemoryEntry
MemoryEntry(memory_type="DECISION", content={"action": "buy"}, source_agent="test")
"""
    result = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
    )
    assert result.returncode != 0, "Expected OSError from uuid4 failure"
    assert "UUID generation service unavailable" in result.stderr, (
        f"Expected error message not found in stderr: {result.stderr}"
    )


@pytest.mark.perf
def test_memory_entry_construction_throughput():
    iterations = 500
    start = time.perf_counter()
    for _ in range(iterations):
        MemoryEntry(**_valid_kwargs())
    elapsed = time.perf_counter() - start
    per_op_ms = (elapsed / iterations) * 1000
    assert per_op_ms < 5.0
