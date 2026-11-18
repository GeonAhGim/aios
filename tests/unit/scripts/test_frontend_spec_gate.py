"""Repository gate: frontend spec documents may not get worse than the recorded baseline."""

from __future__ import annotations

from scripts.frontend_spec import verify_spec


def test_frontend_specs_do_not_exceed_verification_baseline() -> None:
    """New or edited specs must cite real code. Legacy findings are ratcheted down, never up."""
    assert verify_spec.main(["--check"]) == 0
