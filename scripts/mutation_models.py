"""MUT-1 data models for mutation testing.

All domain dataclasses and error types live here so that the engine
(pure logic) and the driver (CLI + subprocess orchestration) share a
single source of truth without importing each other.
"""

from __future__ import annotations

from dataclasses import dataclass


class MutationConfigError(ValueError):
    """Malformed baseline/report file or unrunnable domain config."""


@dataclass(frozen=True)
class DomainSpec:
    """One mutation-testing target. `module_path`/`test_paths` are repo-relative."""

    name: str
    module_path: str
    test_paths: tuple[str, ...]
    deselect: tuple[str, ...] = ()
    timeout: float = 20.0


@dataclass(frozen=True)
class MutationScore:
    """Mutation score for one domain. `incompetent`/`skipped` are excluded from the
    denominator -- an incompetent mutant (invalid code) proves nothing about test
    quality, and a skipped one was never run (filtered operator)."""

    killed: int
    survived: int
    incompetent: int
    skipped: int

    @property
    def scored_total(self) -> int:
        return self.killed + self.survived

    @property
    def total(self) -> int:
        return self.killed + self.survived + self.incompetent + self.skipped

    @property
    def score_percent(self) -> float:
        if self.scored_total == 0:
            return 0.0
        return round(self.killed / self.scored_total * 100, 2)


@dataclass(frozen=True)
class SurvivorRecord:
    module_path: str
    operator_name: str
    definition_name: str | None
    start_line: int
    end_line: int
    diff: str


@dataclass(frozen=True)
class RatchetResult:
    ok: bool
    updated_baseline: dict[str, float]
    messages: tuple[str, ...]


@dataclass
class DomainRunResult:
    domain: str
    score: MutationScore
    survivors: list[SurvivorRecord]
    baseline_ok: bool
    baseline_output: str = ""
