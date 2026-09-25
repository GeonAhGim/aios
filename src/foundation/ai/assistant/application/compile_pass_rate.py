"""U-3a -- compile pass-rate measurement for generated output (pure aggregate).

Spec DoD: "100% of generated scripts either pass DSL compilation/resource-
limit checks, or an explicit failure reason is returned to the user (zero
silent failures, a regression CI fixture)". Since it is "pass OR an explicit
failure reason" rather than "100% pass", what this report measures is
"are there zero silent failures" -- each item in `outcomes` is a
`GenerateScriptResult` from `generate_script` (exactly one of success or
compile-failure, with no ambiguous state in between), so a silent failure is
type-level impossible. This report only aggregates that fact so a
regression CI fixture (a test) can assert it numerically.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal


@dataclass(frozen=True)
class CompilePassRateReport:
    attempted: int
    compiled: int
    failed_with_explicit_reason: int

    @property
    def pass_rate(self) -> Decimal:
        if self.attempted == 0:
            return Decimal(1)
        return Decimal(self.compiled) / Decimal(self.attempted)

    @property
    def silent_failures(self) -> int:
        """Always 0 by definition (enforced by the type system) -- a
        regression fixture asserts this so that, if an ambiguous third state
        is ever added, it gets caught."""
        return self.attempted - self.compiled - self.failed_with_explicit_reason


def build_pass_rate_report(outcome_statuses: Sequence[str]) -> CompilePassRateReport:
    """`outcome_statuses` is the list of each generation attempt's `.status`
    ("compiled" or "compile_failed")."""
    attempted = len(outcome_statuses)
    compiled = sum(1 for s in outcome_statuses if s == "compiled")
    failed = sum(1 for s in outcome_statuses if s == "compile_failed")
    return CompilePassRateReport(
        attempted=attempted, compiled=compiled, failed_with_explicit_reason=failed
    )
