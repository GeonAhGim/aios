"""Tests for src/foundation/evidence/contracts/v1.py - Audit event contract v1.

DoD: negative tests >=3, failure-injection >=1, coverage >=70%.
"""

from datetime import datetime, timezone
from uuid import uuid4

import pytest
from pydantic import BaseModel, ValidationError

from src.foundation.evidence.contracts import v1
from src.foundation.evidence.contracts.v1 import (
    SCHEMA_VERSION,
    AuditEventView,
    AuditTimelinePage,
    Classification,
    Outcome,
    RecordAuditEventCommand,
)

NOW = datetime(2026, 9, 24, tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
# Outcome enum
# ---------------------------------------------------------------------------

class TestOutcome:
    def test_all_expected_members_exist(self) -> None:
        expected = {"SUCCESS", "DENIED", "ERROR"}
        assert {m.name for m in Outcome} == expected

    def test_invalid_string_is_not_member(self) -> None:
        with pytest.raises(ValueError):
            Outcome("UNKNOWN")

    def test_members_are_strings(self) -> None:
        for member in Outcome:
            assert isinstance(member.value, str)
            assert isinstance(member, str)


# ---------------------------------------------------------------------------
# Classification enum
# ---------------------------------------------------------------------------

class TestClassification:
    def test_all_expected_members_exist(self) -> None:
        expected = {
            "PUBLIC",
            "INTERNAL",
            "CONFIDENTIAL",
            "RESTRICTED",
            "SECRET_REFERENCE",
        }
        assert {m.name for m in Classification} == expected

    def test_invalid_string_is_not_member(self) -> None:
        with pytest.raises(ValueError):
            Classification("TOP_SECRET")

    def test_iteration_count(self) -> None:
        assert len(list(Classification)) == 5


# ---------------------------------------------------------------------------
# SCHEMA_VERSION constant
# ---------------------------------------------------------------------------

def test_schema_version_value() -> None:
    assert SCHEMA_VERSION == "v1"


# ---------------------------------------------------------------------------
# RecordAuditEventCommand
# ---------------------------------------------------------------------------

def _command_kwargs(**overrides: object) -> dict[str, object]:
    defaults: dict[str, object] = dict(
        tenant_id=uuid4(),
        aggregate_type="mandate_revision",
        aggregate_id=uuid4(),
        aggregate_revision=1,
        action="mandate_activated",
        outcome=Outcome.SUCCESS,
        actor_subject_id=uuid4(),
        trace_id=uuid4(),
        payload={"purpose": "trading_risk"},
        classification=Classification.INTERNAL,
    )
    defaults.update(overrides)
    return defaults


class TestRecordAuditEventCommand:
    def test_construct_with_all_fields(self) -> None:
        cmd = RecordAuditEventCommand(**_command_kwargs())
        assert cmd.outcome is Outcome.SUCCESS
        assert cmd.classification is Classification.INTERNAL

    def test_tenant_id_none_allowed_for_system_event(self) -> None:
        cmd = RecordAuditEventCommand(**_command_kwargs(tenant_id=None))
        assert cmd.tenant_id is None

    def test_defaults_applied(self) -> None:
        kwargs = _command_kwargs()
        del kwargs["payload"]
        del kwargs["classification"]
        kwargs["aggregate_revision"] = None
        kwargs["actor_subject_id"] = None
        cmd = RecordAuditEventCommand(**kwargs)
        assert cmd.payload == {}
        assert cmd.classification is Classification.INTERNAL
        assert cmd.aggregate_revision is None
        assert cmd.actor_subject_id is None

    def test_missing_required_field_raises(self) -> None:
        kwargs = _command_kwargs()
        del kwargs["aggregate_type"]
        with pytest.raises(ValidationError):
            RecordAuditEventCommand(**kwargs)

    def test_invalid_uuid_for_aggregate_id_raises(self) -> None:
        kwargs = _command_kwargs(aggregate_id="not-a-uuid")
        with pytest.raises(ValidationError):
            RecordAuditEventCommand(**kwargs)

    def test_invalid_outcome_value_raises(self) -> None:
        kwargs = _command_kwargs(outcome="MAYBE")
        with pytest.raises(ValidationError):
            RecordAuditEventCommand(**kwargs)

    def test_invalid_classification_value_raises(self) -> None:
        kwargs = _command_kwargs(classification="TOP_SECRET")
        with pytest.raises(ValidationError):
            RecordAuditEventCommand(**kwargs)

    def test_wrong_type_for_trace_id_raises(self) -> None:
        kwargs = _command_kwargs(trace_id=12345)
        with pytest.raises(ValidationError):
            RecordAuditEventCommand(**kwargs)


# ---------------------------------------------------------------------------
# AuditEventView
# ---------------------------------------------------------------------------

def _view_kwargs(**overrides: object) -> dict[str, object]:
    defaults: dict[str, object] = dict(
        id=uuid4(),
        tenant_id=uuid4(),
        sequence_no=1,
        aggregate_type="mandate_revision",
        aggregate_id=uuid4(),
        aggregate_revision=1,
        action="mandate_activated",
        outcome=Outcome.SUCCESS,
        actor_subject_id=uuid4(),
        trace_id=uuid4(),
        payload_hash="hash-value",
        payload={},
        classification=Classification.INTERNAL,
        previous_hash=None,
        event_hash="event-hash-value",
        occurred_at=NOW,
    )
    defaults.update(overrides)
    return defaults


class TestAuditEventView:
    def test_construct_with_all_fields(self) -> None:
        view = AuditEventView(**_view_kwargs())
        assert view.schema_version == SCHEMA_VERSION

    def test_tenant_id_and_previous_hash_may_be_none(self) -> None:
        view = AuditEventView(**_view_kwargs(tenant_id=None, previous_hash=None))
        assert view.tenant_id is None
        assert view.previous_hash is None

    def test_missing_required_field_raises(self) -> None:
        kwargs = _view_kwargs()
        del kwargs["event_hash"]
        with pytest.raises(ValidationError):
            AuditEventView(**kwargs)

    def test_invalid_sequence_no_type_raises(self) -> None:
        kwargs = _view_kwargs(sequence_no="not-an-int")
        with pytest.raises(ValidationError):
            AuditEventView(**kwargs)

    def test_invalid_occurred_at_raises(self) -> None:
        kwargs = _view_kwargs(occurred_at="not-a-datetime")
        with pytest.raises(ValidationError):
            AuditEventView(**kwargs)

    def test_schema_version_default_can_be_overridden(self) -> None:
        view = AuditEventView(**_view_kwargs(schema_version="v2"))
        assert view.schema_version == "v2"


# ---------------------------------------------------------------------------
# AuditTimelinePage
# ---------------------------------------------------------------------------

class TestAuditTimelinePage:
    def test_construct_with_items(self) -> None:
        item = AuditEventView(**_view_kwargs())
        page = AuditTimelinePage(items=[item], next_cursor=None, as_of=NOW)
        assert page.items == [item]
        assert page.next_cursor is None

    def test_empty_items_allowed(self) -> None:
        page = AuditTimelinePage(items=[], next_cursor=None, as_of=NOW)
        assert page.items == []

    def test_next_cursor_set_means_more_pages(self) -> None:
        page = AuditTimelinePage(items=[], next_cursor="opaque-cursor", as_of=NOW)
        assert page.next_cursor == "opaque-cursor"

    def test_missing_as_of_raises(self) -> None:
        with pytest.raises(ValidationError):
            AuditTimelinePage(items=[], next_cursor=None)

    def test_items_with_wrong_element_type_raises(self) -> None:
        with pytest.raises(ValidationError):
            AuditTimelinePage(items=[{"not": "a view"}], next_cursor=None, as_of=NOW)


# ---------------------------------------------------------------------------
# Failure injection - construction must not silently swallow a dependency
# failure raised inside pydantic's own validation machinery.
# ---------------------------------------------------------------------------

class TestFailureInjection:
    def test_validation_error_from_pydantic_internals_propagates(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Contracts in this module do no I/O and add no error handling of
        their own on top of pydantic. If pydantic's validator raises, that
        exception must reach the caller unmodified (fail-closed, no silent
        success path)."""

        original_init = BaseModel.__init__

        def _boom(self: BaseModel, **data: object) -> None:
            if getattr(self, "__class__", None) is RecordAuditEventCommand:
                raise RuntimeError("simulated dependency failure")
            original_init(self, **data)

        monkeypatch.setattr(BaseModel, "__init__", _boom)

        with pytest.raises(RuntimeError, match="simulated dependency failure"):
            RecordAuditEventCommand(**_command_kwargs())

    def test_module_exposes_no_hidden_error_suppression(self) -> None:
        """Sanity check that v1.py contains no try/except that could mask a
        validation failure as a successful contract construction."""
        import inspect

        source = inspect.getsource(v1)
        assert "except" not in source
