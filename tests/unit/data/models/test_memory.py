import time

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


def test_memory_entry_construction_throughput():
    iterations = 500
    start = time.perf_counter()
    for _ in range(iterations):
        MemoryEntry(**_valid_kwargs())
    elapsed = time.perf_counter() - start
    per_op_ms = (elapsed / iterations) * 1000
    assert per_op_ms < 5.0
