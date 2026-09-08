"""IND-13 nightly entry point -- full reference-vector catalog verification.

Spec: docs/specs/L4_analytics_authoring_backtest_marketplace_v1.0.md §9.9
IND-13. Wraps `src.core.indicators.reference.verify_job.run_nightly_full()`,
which itself only calls IND-7g's `verify_all.run_verification` -- no
cross-verification logic lives here.

Deliberately *not* a pytest test: every-CI pytest only runs the 30-name
deterministic sample (tests/unit/core/indicators/test_reference_sampling.py,
no `nightly` marker so it always runs). This script is the full-corpus path,
run from the nightly job runner (see local_ci.py / nightly wiring), so the
full catalog gets exercised without slowing down every commit (§C, ADR-2026
-09-06-G §1: no gate that never runs).

Usage: `python scripts/verify_indicators_nightly.py`. Exit 0 = every
verifiable indicator (`verify_all.VERIFIABLE_NAMES`) three-way matched, or
every mismatch is a cited, pre-known exclusion
(`verify_job.KNOWN_UNVERIFIED`). Exit 1 = at least one indicator diverged
outside that allowlist -- printed as `MISMATCH <name> ...` lines.
"""
from __future__ import annotations

import logging

from src.core.indicators.reference import verify_job

logger = logging.getLogger(__name__)


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    report = verify_job.run_nightly_full()
    unexpected = [n for n in report.excluded if n not in verify_job.KNOWN_UNVERIFIED]
    logger.info(
        "nightly verify: verified=%d excluded=%d unexpected=%d",
        len(report.verified), len(report.excluded), len(unexpected),
    )  # fmt: skip
    for m in report.mismatches:
        logger.warning(
            "MISMATCH %s dataset=%s output=%s idx=%s err=%.3e talib=%s inc=%s vec=%s",
            m.name, m.dataset, m.output, m.index, m.worst_rel_error,
            m.talib_value, m.incremental_value, m.vectorized_value,
        )  # fmt: skip
    return 1 if unexpected else 0


if __name__ == "__main__":
    raise SystemExit(main())
