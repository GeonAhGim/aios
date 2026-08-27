from src.data.models.base import ProvenanceStatus
from src.data.models.memory import MemoryEntry, MemoryType


def test_memory_entry_defaults():
    entry = MemoryEntry(
        memory_type=MemoryType.DECISION,
        content={"action": "buy"},
        source_agent="strategy-agent",
    )
    assert entry.status == ProvenanceStatus.UNVERIFIED
    assert entry.confidence == 0.5
    assert entry.verified_at is None


def test_memory_entry_confidence_bounds():
    import pytest
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        MemoryEntry(
            memory_type=MemoryType.EPISODIC,
            content={},
            source_agent="a",
            confidence=1.5,
        )
