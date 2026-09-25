"""Prompt registry -- pure functions/value objects, no I/O.

Spec: docs/specs/L4_ai_research_strategy_factory_v1.0.md §2.2 AI-5
`domain/prompt_registry.py` ("prompt version/hash, a reproducibility-key
component"), §1 "reproducibility" (reproducibility key =
script_hash‖data_lineage‖config‖model_hash) --
`prompt_hash` is this module's contribution to that key, consumed by
`StrategyProposal.prompt_hash` (factory `contracts/v1.py`, AI-8, not yet
implemented).

Hashing goes through `src.core.risk.hashing.canonical_json`/`sha256_hex`
(R-01, same rule CLAUDE.md #3 protects for money: one logical value must
always serialize to the same bytes) -- this module never reimplements JSON
canonicalization or calls Python's salted `hash()`.
"""

from __future__ import annotations

from dataclasses import dataclass

from src.core.risk.hashing import canonical_json, sha256_hex


class PromptRegistryError(Exception):
    """Common base for prompt registry rule violations."""


class PromptVersionNotFoundError(PromptRegistryError):
    """Lookup miss -- the caller (AI-6/7/7b adapters, not yet implemented)
    must not fall back to an unregistered/ad-hoc prompt string; every
    prompt used to produce a `StrategyProposal` must be traceable back to a
    registered `(prompt_id, version)` pair (fail-closed, no silent default)."""


class DuplicatePromptVersionError(PromptRegistryError):
    """A `(prompt_id, version)` pair is immutable once registered -- the
    reproducibility key would otherwise silently point at different prompt
    text depending on registration order."""

    def __init__(self, prompt_id: str, version: int) -> None:
        self.prompt_id = prompt_id
        self.version = version
        super().__init__(f"prompt {prompt_id!r} version {version} already registered")


@dataclass(frozen=True)
class PromptTemplate:
    """A single immutable prompt version. `template` is the literal prompt
    text (parameter substitution, if any, happens downstream in the
    provider adapter -- this module only registers/hashes the template
    itself, not a rendered instance of it)."""

    prompt_id: str
    version: int
    template: str

    def __post_init__(self) -> None:
        if not self.prompt_id:
            raise PromptRegistryError("prompt_id must not be empty")
        if self.version < 1:
            raise PromptRegistryError("version must be >= 1")
        if not self.template:
            raise PromptRegistryError("template must not be empty")


def prompt_hash(template: PromptTemplate) -> str:
    """The reproducibility-key component this module owns. Deterministic
    across processes and runs because it goes through `canonical_json`
    (sorted keys, fixed separators) -- Python's built-in `hash()` is
    per-process-salted for strings and would make the reproducibility key
    unreproducible across two runs of the same proposal."""
    payload = {
        "prompt_id": template.prompt_id,
        "version": template.version,
        "template": template.template,
    }
    return sha256_hex(canonical_json(payload))


@dataclass(frozen=True)
class PromptRegistry:
    """Pure, immutable registry keyed by `(prompt_id, version)`.

    `register()` returns a *new* `PromptRegistry` rather than mutating
    `entries` in place -- a caller holding a reference to an older snapshot
    never observes templates registered after that snapshot was taken (the
    same immutability discipline as `token_rules.AgentToken`/
    `confirm.ConfirmTicket`). Persisting a registration durably is an
    adapter's job (not yet implemented); this type is the in-memory pure
    rule (uniqueness, lookup, "latest version").
    """

    entries: dict[tuple[str, int], PromptTemplate]

    @staticmethod
    def empty() -> PromptRegistry:
        return PromptRegistry(entries={})

    def register(self, template: PromptTemplate) -> PromptRegistry:
        key = (template.prompt_id, template.version)
        if key in self.entries:
            raise DuplicatePromptVersionError(template.prompt_id, template.version)
        return PromptRegistry(entries={**self.entries, key: template})

    def get(self, prompt_id: str, version: int) -> PromptTemplate:
        try:
            return self.entries[(prompt_id, version)]
        except KeyError:
            raise PromptVersionNotFoundError(f"{prompt_id} v{version} not registered") from None

    def latest_version(self, prompt_id: str) -> int:
        versions = [v for (pid, v) in self.entries if pid == prompt_id]
        if not versions:
            raise PromptVersionNotFoundError(f"{prompt_id} has no registered versions")
        return max(versions)
