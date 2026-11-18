"""NoActiveMandateError must be one class so the exception registry maps it to 404."""

from __future__ import annotations

import ast
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest

from src.api.contracts.error_codes import ErrorCode
from src.api.contracts.exception_mapping import map_exception
from src.foundation.mandates.application import evaluate_policy, pause_mandate, propose_amendment
from src.foundation.mandates.application.errors import NoActiveMandateError
from src.foundation.mandates.contracts.v1 import MandateRuleInput


class _NoMandateRepo:
    async def get_mandate(self, tenant_id: object) -> None:
        return None


def test_error_classes_are_one_object() -> None:
    """Red repro: before unification the three modules each defined their own class."""
    classes = {
        evaluate_policy.NoActiveMandateError,
        pause_mandate.NoActiveMandateError,
        propose_amendment.NoActiveMandateError,
    }
    assert classes == {NoActiveMandateError}


def test_only_one_definition_in_package() -> None:
    root = Path(pause_mandate.__file__).resolve().parents[1]
    defs = []
    for path in root.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        defs += [
            path.name
            for node in ast.walk(tree)
            if isinstance(node, ast.ClassDef) and node.name == "NoActiveMandateError"
        ]
    assert defs == ["errors.py"]


def test_map_exception_is_resource_not_found() -> None:
    code, _, _ = map_exception(NoActiveMandateError("t"))
    assert code == ErrorCode.RESOURCE_NOT_FOUND


@pytest.mark.parametrize("path", ["pause", "resume", "amend"])
async def test_paths_without_mandate_map_to_not_found(path: str) -> None:
    repo: Any = _NoMandateRepo()
    tenant = uuid4()
    with pytest.raises(Exception) as info:  # noqa: PT011 - the mapped type is asserted below
        if path == "pause":
            await pause_mandate.pause_mandate(repo, tenant_id=tenant)
        elif path == "resume":
            await pause_mandate.resume_mandate(repo, tenant_id=tenant)
        else:
            await propose_amendment.propose_amendment(
                repo,
                tenant_id=tenant,
                rules=MandateRuleInput.model_construct(),
            )
    code, _, _ = map_exception(info.value)
    assert code == ErrorCode.RESOURCE_NOT_FOUND
