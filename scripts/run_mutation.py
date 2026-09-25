"""MUT-1 mutation testing driver for ledger/risk/oms domain logic.

Engine choice: cosmic-ray, not mutmut. mutmut>=3 refuses to run on native Windows
("please use the WSL" -- github.com/boxed/mutmut#397) and this repo's CI runners are
native Windows (`C:\\aios\\pm\\local_ci.py`, per pyproject.toml's pytest-timeout
comment). cosmic-ray has no such restriction and was verified end-to-end on this
machine before writing this script. Both are already present in the dev venv;
`[project.optional-dependencies].mutation` below pins cosmic-ray as the declared one.

Operator scope: cosmic-ray's full operator catalog is a pairwise matrix (every
binary operator can become any of the other ~10), which explodes mutant counts on
files with many arithmetic/comparison sites without adding much signal beyond a
smaller, well-established "did you get sign/boundary/branch logic right" subset.
ALLOWED_OPERATORS below is that subset (number literals, comparison-boundary swaps,
arithmetic sign swaps, boolean logic, break/continue, unary sign flip, empty-loop).
Unlisted operators are marked SKIPPED via cosmic-ray's operators-filter and excluded
from the score denominator (see compute_score) -- this keeps a domain run inside a
single foreground command's timeout budget (measured ~1.5-2.5s per mutant on this
repo's unit suites; see docstring of DOMAINS below for the counts this produced).

Usage:
    python scripts/run_mutation.py --domain all
    python scripts/run_mutation.py --domain oms --top 30

Exit codes: 0 = no domain regressed past --tolerance against the baseline file
(mutation-score-baseline.json, ratchets upward like scripts/coverage_ratchet.py).
1 = a domain's score dropped, or a domain's own test suite fails unmutated (can't
measure a red baseline -- fail-closed). 2 = usage/config error.
"""

from __future__ import annotations

import argparse
import json
import re
import shlex
import subprocess
import sys
import tempfile
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from cosmic_ray.work_db import WorkDB
from cosmic_ray.work_item import TestOutcome, WorkerOutcome, WorkItem, WorkResult

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_BASELINE = ROOT / "mutation-score-baseline.json"
DEFAULT_REPORT_OUT = ROOT / "mutation-report.json"
DEFAULT_TOLERANCE_PP = 0.5
DEFAULT_TOP_SURVIVORS = 30

# core/ prefix curated subset -- see module docstring "Operator scope".
ALLOWED_OPERATORS = frozenset(
    {
        "core/NumberReplacer",
        "core/ReplaceComparisonOperator_Lt_LtE",
        "core/ReplaceComparisonOperator_LtE_Lt",
        "core/ReplaceComparisonOperator_Gt_GtE",
        "core/ReplaceComparisonOperator_GtE_Gt",
        "core/ReplaceComparisonOperator_Eq_NotEq",
        "core/ReplaceComparisonOperator_NotEq_Eq",
        "core/ReplaceBinaryOperator_Add_Sub",
        "core/ReplaceBinaryOperator_Sub_Add",
        "core/ReplaceBinaryOperator_Mul_Div",
        "core/ReplaceBinaryOperator_Div_Mul",
        "core/AddNot",
        "core/ReplaceTrueWithFalse",
        "core/ReplaceFalseWithTrue",
        "core/ReplaceAndWithOr",
        "core/ReplaceOrWithAnd",
        "core/ReplaceBreakWithContinue",
        "core/ReplaceContinueWithBreak",
        "core/ReplaceUnaryOperator_USub_UAdd",
        "core/ReplaceUnaryOperator_UAdd_USub",
        "core/ZeroIterationForLoop",
    }
)


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


DOMAINS: dict[str, DomainSpec] = {
    "ledger": DomainSpec(
        name="ledger",
        module_path="src/foundation/ledger/domain",
        test_paths=("tests/foundation/unit/ledger",),
    ),
    "risk": DomainSpec(
        name="risk",
        module_path="src/core/risk",
        test_paths=("tests/unit/core/risk",),
    ),
    "oms": DomainSpec(
        name="oms",
        module_path="src/services/oms/domain",
        test_paths=("tests/unit/oms",),
        # These two spawn their own child pytest subprocess as a gate-red-repro
        # self-test (see tests/unit/oms/test_algo_slicer.py) -- ~2.2s each, added
        # 250x over the mutant run they'd nearly double the domain's wall-clock
        # without adding mutation-detection signal (they exercise a copied module,
        # not the file cosmic-ray actually mutates on disk).
        deselect=(
            "tests/unit/oms/test_repository_ports.py::"
            "test_pytest_gate_turns_red_when_command_type_literal_is_widened",
            "tests/unit/oms/test_algo_slicer.py::"
            "test_pytest_gate_turns_red_when_remainder_absorption_is_removed",
        ),
    ),
}


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


@dataclass(frozen=True)
class SurvivorRecord:
    module_path: str
    operator_name: str
    definition_name: str | None
    start_line: int
    end_line: int
    diff: str


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
    domain: DomainSpec,
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


@dataclass(frozen=True)
class RatchetResult:
    ok: bool
    updated_baseline: dict[str, float]
    messages: tuple[str, ...]


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


@dataclass
class DomainRunResult:
    domain: str
    score: MutationScore
    survivors: list[SurvivorRecord]
    baseline_ok: bool
    baseline_output: str = ""


def _run(cmd: list[str], cwd: Path, timeout: float) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        cmd,
        cwd=cwd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        check=False,
    )


def run_domain(
    domain: DomainSpec,
    python_exe: Path,
    work_dir: Path,
    top: int = DEFAULT_TOP_SURVIVORS,
) -> DomainRunResult:
    """Drive one cosmic-ray session end to end: verify the domain's own tests pass
    unmutated (`cosmic-ray baseline`, fail-closed if not), init the work DB, filter
    to ALLOWED_OPERATORS, exec every remaining mutant, then read results back via
    the WorkDB Python API (no shelling out to cr-rate/cr-report for parsing)."""
    from cosmic_ray import plugins as cr_plugins

    module_path = ROOT / domain.module_path
    exclude_patterns = exclude_operator_patterns(cr_plugins.operator_names(), ALLOWED_OPERATORS)
    config_text = build_config_toml(domain, module_path, python_exe, exclude_patterns)
    config_path = work_dir / f"{domain.name}.toml"
    session_path = work_dir / f"{domain.name}.sqlite"
    config_path.write_text(config_text, encoding="utf-8")

    try:
        baseline = _run(
            [str(python_exe), "-m", "cosmic_ray.cli", "baseline", str(config_path)],
            cwd=ROOT,
            timeout=120,
        )
        if baseline.returncode != 0:
            return DomainRunResult(
                domain=domain.name,
                score=MutationScore(0, 0, 0, 0),
                survivors=[],
                baseline_ok=False,
                baseline_output=(baseline.stdout + baseline.stderr)[-4000:],
            )

        init = _run(
            [str(python_exe), "-m", "cosmic_ray.cli", "init", str(config_path), str(session_path)],
            cwd=ROOT,
            timeout=120,
        )
        if init.returncode != 0:
            raise MutationConfigError(
                f"{domain.name}: cosmic-ray init 실패: {init.stdout}{init.stderr}"
            )

        filt = _run(
            [
                str(python_exe),
                "-m",
                "cosmic_ray.tools.filters.operators_filter",
                str(session_path),
                str(config_path),
            ],
            cwd=ROOT,
            timeout=60,
        )
        if filt.returncode != 0:
            raise MutationConfigError(
                f"{domain.name}: operators-filter 실패: {filt.stdout}{filt.stderr}"
            )

        exec_timeout = max(600.0, domain.timeout * 40)
        run = _run(
            [str(python_exe), "-m", "cosmic_ray.cli", "exec", str(config_path), str(session_path)],
            cwd=ROOT,
            timeout=exec_timeout,
        )
        if run.returncode != 0:
            raise MutationConfigError(
                f"{domain.name}: cosmic-ray exec 실패: {run.stdout}{run.stderr}"
            )

        db = WorkDB(str(session_path), WorkDB.Mode.open)
        try:
            results_by_job = dict(db.results)
            score = compute_score(results_by_job.values())
            survivors = classify_survivors(db.work_items, results_by_job, limit=top)
        finally:
            db.close()

        return DomainRunResult(
            domain=domain.name, score=score, survivors=survivors, baseline_ok=True
        )
    finally:
        # cosmic-ray mutates module_path in place and restores it in a
        # finally-block of its own -- but if this process's own timeout kills the
        # exec subprocess mid-mutation (SIGKILL, no chance to run Python finally),
        # the source file can be left mutated on disk. Always verify and restore.
        _restore_if_dirty(module_path)


def _restore_if_dirty(module_path: Path) -> None:
    status = subprocess.run(
        ["git", "status", "--porcelain", "--", str(module_path)],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    if status.stdout.strip():
        subprocess.run(
            ["git", "checkout", "--", str(module_path)],
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )


def _print_report(results: Sequence[DomainRunResult]) -> None:
    print(
        f"{'domain':<10} {'killed':>7} {'survived':>9} {'incompetent':>12} "
        f"{'skipped':>8} {'score':>8}"
    )
    for r in results:
        s = r.score
        print(
            f"{r.domain:<10} {s.killed:>7} {s.survived:>9} {s.incompetent:>12} "
            f"{s.skipped:>8} {s.score_percent:>7.2f}%"
        )
    for r in results:
        if not r.survivors:
            continue
        print(f"\n--- {r.domain}: top {len(r.survivors)} survivors ---")
        for rec in r.survivors:
            print(
                f"  {rec.module_path}:{rec.start_line} {rec.operator_name} ({rec.definition_name})"
            )


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--domain", choices=[*DOMAINS, "all"], default="all")
    parser.add_argument("--top", type=int, default=DEFAULT_TOP_SURVIVORS)
    parser.add_argument("--baseline", type=Path, default=DEFAULT_BASELINE)
    parser.add_argument("--tolerance", type=float, default=DEFAULT_TOLERANCE_PP)
    parser.add_argument("--report-out", type=Path, default=DEFAULT_REPORT_OUT)
    parser.add_argument("--python", type=Path, default=Path(sys.executable))
    args = parser.parse_args(argv)

    domain_names = list(DOMAINS) if args.domain == "all" else [args.domain]

    try:
        prior_baseline = read_baseline(args.baseline)
    except MutationConfigError as exc:
        print(f"FAIL: {exc}")
        return 2

    with tempfile.TemporaryDirectory(prefix="aios_mutation_") as tmp:
        work_dir = Path(tmp)
        results: list[DomainRunResult] = []
        for name in domain_names:
            result = run_domain(DOMAINS[name], args.python, work_dir, top=args.top)
            results.append(result)
            if not result.baseline_ok:
                print(f"FAIL: {name} 도메인 테스트가 변이 없이도 실패한다(측정 불가):")
                print(result.baseline_output)
                return 1

        _print_report(results)

        report = {
            r.domain: {
                "killed": r.score.killed,
                "survived": r.score.survived,
                "incompetent": r.score.incompetent,
                "skipped": r.score.skipped,
                "score_percent": r.score.score_percent,
                "survivors": [
                    {
                        "module_path": s.module_path,
                        "operator_name": s.operator_name,
                        "definition_name": s.definition_name,
                        "start_line": s.start_line,
                        "end_line": s.end_line,
                        "diff": s.diff,
                    }
                    for s in r.survivors
                ],
            }
            for r in results
        }
        args.report_out.write_text(json.dumps(report, indent=2), encoding="utf-8")

        current_scores = {r.domain: r.score.score_percent for r in results}
        ratchet = evaluate_ratchet(current_scores, prior_baseline, tolerance=args.tolerance)
        for msg in ratchet.messages:
            print(msg)
        write_baseline(args.baseline, ratchet.updated_baseline)
        return 0 if ratchet.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
