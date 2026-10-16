"""MUT-1 pure logic engine — score aggregation, operator filtering, TOML config,
baseline ratchet.

No subprocess invocation, no cosmic-ray process dependency, no file I/O
(except baseline read/write helpers). Every function is unit-testable with
synthetic WorkResult/WorkItem objects.
"""

from __future__ import annotations

import json
import re
import shlex
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path

from cosmic_ray.work_item import TestOutcome, WorkerOutcome, WorkItem, WorkResult

from scripts.mutation_models import (
    MutationConfigError,
    MutationScore,
    RatchetResult,
    SurvivorRecord,
)

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_TOLERANCE_PP = 0.5
DEFAULT_TOP_SURVIVORS = 30


def compute_score(results: Iterable[WorkResult]) -> MutationScore:
    """Pure aggregation over cosmic-ray WorkResults -- no I/O, no cosmic-ray process
    invocation. Handles the empty-results case (score_percent=0.0, not ZeroDivisionError)."""
    counts: Counter[str] = Counter()
    for result in results:
        if result.worker_outcome == WorkerOutcome.SKIPPED:
            counts["skipped"] += 1
        elif result.test_outcome == TestOutcome.INCOMPETENT:
            counts["incompetent"] += 1
        elif result.test_outcome == TestOutcome.SURVIVED:
            counts["survived"] += 1
        elif result.test_outcome == TestOutcome.KILLED:
            counts["killed"] += 1
        else:
            # WorkerOutcome.NO_TEST / ABNORMAL with no test_outcome -- couldn't run,
            # same bucket as filtered: doesn't count for or against the suite.
            counts["skipped"] += 1
    return MutationScore(
        killed=counts["killed"],
        survived=counts["survived"],
        incompetent=counts["incompetent"],
        skipped=counts["skipped"],
    )


def classify_survivors(
    work_items: Sequence[WorkItem],
    results: Mapping[str, WorkResult],
    limit: int = DEFAULT_TOP_SURVIVORS,
) -> list[SurvivorRecord]:
    """Top `limit` surviving mutants, sorted deterministically (module path then
    source position) so repeated runs over the same code produce the same ordering
    for a human to read down when triaging harmless-vs-defect-candidate."""
    records: list[SurvivorRecord] = []
    for item in work_items:
        result = results.get(item.job_id)
        if result is None or result.test_outcome != TestOutcome.SURVIVED:
            continue
        if not item.mutations:
            continue
        mutation = item.mutations[0]
        records.append(
            SurvivorRecord(
                module_path=mutation.module_path.as_posix(),
                operator_name=mutation.operator_name,
                definition_name=mutation.definition_name,
                start_line=mutation.start_pos[0],
                end_line=mutation.end_pos[0],
                diff=result.diff or "",
            )
        )
    records.sort(key=lambda r: (r.module_path, r.start_line, r.operator_name))
    return records[:limit]


def exclude_operator_patterns(
    all_operator_names: Iterable[str], allowed: frozenset[str]
) -> list[str]:
    """Regex patterns (one literal-anchored pattern per excluded operator) for
    cosmic-ray's operators-filter `exclude-operators` config key. Pure function of
    the full operator catalog + our allowlist -- doesn't need cosmic-ray plugins
    loaded to unit test (the catalog is passed in)."""
    return sorted(re.escape(name) + "$" for name in all_operator_names if name not in allowed)


def build_config_toml(
    domain,  # DomainSpec — avoid circular import; validated at call site
    module_path: Path,
    python_exe: Path,
    exclude_patterns: Sequence[str],
) -> str:
    """Render the cosmic-ray TOML config as text (no `toml` package dependency --
    the structure is fixed and simple enough for direct string building). All paths
    are forward-slash: cosmic-ray's test runner splits `test-command` with
    `shlex.split` in POSIX mode, which treats backslashes as escapes and would
    mangle a native Windows path."""
    test_cmd_parts = [
        python_exe.as_posix(),
        "-m",
        "pytest",
        "-q",
        "-p",
        "no:cacheprovider",
        "--no-header",
    ]
    for nodeid in domain.deselect:
        test_cmd_parts += ["--deselect", nodeid]
    test_cmd_parts += [(ROOT / p).as_posix() for p in domain.test_paths]
    test_command = " ".join(shlex.quote(part) for part in test_cmd_parts)

    exclude_toml = ", ".join(json.dumps(p) for p in exclude_patterns)
    return (
        "[cosmic-ray]\n"
        f"module-path = {json.dumps(module_path.as_posix())}\n"
        f"timeout = {domain.timeout}\n"
        "excluded-modules = []\n"
        f"test-command = {json.dumps(test_command)}\n"
        "\n"
        "[cosmic-ray.distributor]\n"
        'name = "local"\n'
        "\n"
        "[cosmic-ray.filters.operators-filter]\n"
        f"exclude-operators = [{exclude_toml}]\n"
    )


def evaluate_ratchet(
    current: Mapping[str, float],
    baseline: Mapping[str, float] | None,
    tolerance: float = DEFAULT_TOLERANCE_PP,
) -> RatchetResult:
    """Pure ratchet judgement -- no file I/O. Mirrors scripts/coverage_ratchet.py:
    a domain's score may not drop by more than `tolerance` percentage points below
    its recorded baseline; rising scores overwrite the baseline (one-way ratchet).
    Domains missing from `current` (not run this invocation) keep their recorded
    baseline unchanged. A domain absent from `baseline` (first run, or a new domain
    added to DOMAINS) always passes and seeds the baseline."""
    updated = dict(baseline) if baseline else {}
    messages: list[str] = []
    ok = True
    for domain, score in current.items():
        prior = updated.get(domain)
        if prior is None:
            updated[domain] = score
            messages.append(f"{domain}: BASELINE init {score:.2f}%")
            continue
        delta = score - prior
        if delta < -tolerance:
            ok = False
            messages.append(
                f"{domain}: FAIL {prior:.2f}% -> {score:.2f}% ({delta:+.2f}pp, "
                f"tolerance {tolerance:.2f}pp exceeded)"
            )
            continue
        if score > prior:
            updated[domain] = score
            messages.append(f"{domain}: OK, baseline raised {prior:.2f}% -> {score:.2f}%")
        else:
            messages.append(f"{domain}: OK {score:.2f}% (baseline {prior:.2f}%)")
    return RatchetResult(ok=ok, updated_baseline=updated, messages=tuple(messages))


def read_baseline(path: Path) -> dict[str, float] | None:
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise MutationConfigError(f"baseline 파일 JSON 파싱 실패: {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise MutationConfigError(f"baseline 파일 형식 오류(object 아님): {path}")
    try:
        return {str(k): float(v) for k, v in data.items()}
    except (TypeError, ValueError) as exc:
        raise MutationConfigError(f"baseline 값이 숫자가 아님: {path}: {exc}") from exc


def write_baseline(path: Path, scores: Mapping[str, float]) -> None:
    path.write_text(
        json.dumps({k: round(v, 2) for k, v in sorted(scores.items())}, indent=2) + "\n",
        encoding="utf-8",
    )
