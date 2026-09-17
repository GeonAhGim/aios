"""U-3a application-layer exceptions.
exception_registry_foundation_ai_assistant.py maps these to ErrorCode."""

from __future__ import annotations


class AssistantFeatureDisabledError(Exception):
    """The `FF_U3_AI_ASSISTANT` feature flag is off -- mapped to 404 (does
    not pretend to exist; see the §U common DoD "fully inactive when the
    flag is off")."""
