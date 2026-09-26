from __future__ import annotations

import pytest

from src.foundation.automation.flags import FEATURE_FLAG_NAME


@pytest.fixture(autouse=True)
def _automation_flag_on(monkeypatch: pytest.MonkeyPatch) -> None:
    """The kill-switch adversarial test exercises `execute_action`'s gate
    check, not the U-4a feature flag -- default the flag ON so a DENY here
    is provably the gate's doing, not the flag short-circuiting first."""
    monkeypatch.setenv(FEATURE_FLAG_NAME, "1")
