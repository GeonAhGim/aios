# CLAUDE.md

Guidance for coding agents working in this repository (aios). Loaded automatically each session.
When in doubt, the leaf spec (`docs/specs/L4_*.md`) and `docs/design/INVARIANTS.md` win over this file.

## 1. Repository map

- `src/core/` — pure domain rules, no I/O: `strategy/`, `portfolio/`, `risk/`, `executor/`, plus
  `indicators`, `eventstore`, `safety`, `security`, `validator`, `scanner`, `notifications`,
  `observability`, `rate_limit`, `approval`. `strategy|portfolio|risk|executor` are
  FROZEN_PAPER_ONLY — do not edit unless the task's `decision` field records an explicit FROZEN
  approval.
- `src/foundation/` — bounded-context aggregates with adapters: `allocation`, `backtest`,
  `charting`, `connections`, `ems`, `entities`, `evidence`, `execution_ownership`, `ledger`,
  `mandates`, `market_data`, `marketplace`, `paper_control`, `performance`, `positions`,
  `reconciliation`, `research_data`, `risk`.
- `src/services/` — application services orchestrating foundation/core (`execution_loop`, `auth`,
  `capital_allocation`, `condition_compiler`, `background_loops`, ...).
- `src/api/` — FastAPI routers/schemas/deps/contracts. New endpoints must be wired in
  `router_registry.py` and kept in sync with `contracts/openapi/v1.json`; drift is a ghost path
  caught by `check_consistency.py` (see Frequent mistakes #7).
- `src/exchanges/` — adapters: `bitget/`, `kis/`, `nh/`, `paper/`, `common/`, `factory.py`.
- `src/db/migrations/versions/` — Alembic revisions (see Commands, single head required).
- `frontend/apps/web` — Next.js app. `frontend/packages/{api-client,chart-engine,shared-hooks,
  shared-types,ui-web}` — shared packages.
- `docs/specs/L4_*.md` — leaf spec of record (§2 module table, §9 leaf DoD).
- `docs/design/` — ADRs and `INVARIANTS.md` (I-01..I-11, binding on every leaf).
- `docs/ideabank/**`, `docs/blue_team/**`, `docs/red_team/**` — non-normative, see §8.
- `scripts/check_*.py` — static CI gates (AST/text scanners, no DB required).

## 2. Commands

- Tests: scope to what you touched — `<venv-python> -m pytest <paths> -q -p no:cacheprovider`.
  Real-DB integration tests read `TEST_DATABASE_URL`.
- Lint/type: `<venv-python> -m ruff check src tests scripts`, `<venv-python> -m mypy src`.
- Gates: run only the `scripts/check_*.py` your change can affect, e.g. `check_zone_manifest.py`,
  `check_migration_chain.py`, `check_position_key_central.py`, `check_consistency.py`,
  `check_code_language.py`, `check_code_ratchets.py`.
- Alembic: exactly one head at all times — `check_migration_chain.py` fails on 2+ heads or a
  broken `down_revision` chain. Every revision needs a working `downgrade()`. Confirm the current
  head before branching a new revision off it (see Frequent mistakes #9).
- `.venv` is per-worktree; if missing, fall back to `C:\aios\aios\.venv\Scripts\python.exe`.

## 3. Rules

- Comments/docstrings under `src/` are English only (ADR-2026-09-07-A); docs/ADRs/specs/commit
  messages/task notes stay Korean.
- Monetary amounts are `Decimal`, never `float`. All datetimes are timezone-aware UTC.
- Writes to hot tables follow the standard-105 pattern: conditional UPDATE / `SELECT ... FOR
  UPDATE` / idempotency key. Default posture is fail-closed.
- `position_key` values are produced only via `PositionKey` / `PositionKey.parse()`
  (`domain/position_key.py`) — never assembled with f-strings, `+`, or `.join()`
  (`check_position_key_central.py` enforces this with an AST scan).
- Unverified external facts (exchange docs, undocumented endpoints) raise `NotImplementedError`
  with a `# ratchet-allow: <reason>` comment in the file's first 20 lines, instead of a guessed
  implementation (`check_code_ratchets.py` / `check_consistency.py`).
- One leaf = one commit. Commit message is `<type>(<scope>): <summary>`, with the task id and the
  spec leaf id (e.g. `FA-0d`, `L4-07`) in the body.
- Every leaf has at least one negative test.

## 4. Prohibited

- Running the full suite (`pytest tests/`, ~26 min) — scope to touched paths; CI runs the full
  suite for you.
- `run_in_background` for gate commands (pytest/ruff/mypy/...) — this is a single-turn headless
  run; wait for every command in the foreground.
- Bare `git stash` (the stash stack is shared across worktrees) and `git add -A` / `git commit`
  without explicit paths (drags in another session's staged files).
- Touching `C:\aios\pm` (task files, orchestrator, escalations) with git or a raw editor. Task
  status updates go through the `python -c "...json.load/update/dump..."` pattern only, and only
  against your own task file.
- Printing secrets (`.env` values, API keys, JWT signing keys) to logs or commit messages.
- `git show <sha>` on a commit touching 20+ files — use `--stat` first, then `-- <path>` for the
  paths you actually need.

## 5. Definition of done (D2 floor, ADR-2026-09-09-C)

A leaf is not done below D2 evidence: negative tests ≥3, one failure-injection test, one numeric
performance assertion (p95/p99 or throughput, against the budget table in ADR-2026-09-09-C
Decision 1), and one red-gate reproduction. Safety/execution/ledger/compliance/data axes (`R`,
`L4`, `LA/LB/LC`, `FA`, `CM`, `EO`, `DC`) need D3: an adversarial test cross-checked against
`INVARIANTS.md`, plus a passing `replay_verify`. If a failure mode genuinely does not apply, write
`N/A(<reason>)` instead of forcing the checklist — the checklist is a floor, not the goal
(ADR-2026-09-10-C Decision 4).

## 6. Frequent mistakes

1. Ending a turn on "waiting for background process" — a headless worker gets exactly one turn;
   nothing after it runs.
2. `note`/`decision` text with an unescaped quote or newline corrupts `tasks/task-<id>.json` and
   stalls the whole orchestrator — always write task JSON through the `python -c` json pattern.
3. `git commit` without `-- <paths>`, or `git add -A`, sweeping up another session's staged hunks.
4. Running `pytest tests/` "to be safe" burns the turn budget and can kill the task with
   `error_max_turns` — scope to touched paths.
5. Re-reading a file right after `Edit` to confirm it, and re-running the full gate sequence after
   every small change instead of batching edits and gating once.
6. Wiring a new `scripts/check_*.py` gate straight into CI as a hard failure — if `main` already
   violates it, CI locks red on the introducing commit; register it as `warn` + baseline first.
7. Adding a router/endpoint without updating `contracts/openapi/v1.json`, leaving a ghost path for
   `check_consistency.py` to flag later instead of at introduction.
8. Swapping a third-party dependency (e.g. an indicator library) without an explicit architecture
   decision on record — an environment constraint alone does not authorize a dependency swap.
9. Creating an Alembic revision before its parent revision id is confirmed, branching the
   migration chain into two heads — record the intended parent in the task note and wait for
   `decision`, unless one is already recorded.
10. `git show <sha>` on a wide commit "to see what changed" — use `--stat` then target specific
    paths instead.
11. Deleting a file under `src/**/generated/` (e.g. `src/exchanges/kis/generated/*_tr_labels.py`)
    because it has zero import/string references — for these specific generator outputs that is
    expected (BR-12/ADR-2026-09-06-I D7 moves Korean labels out of docstrings into plain dict
    literals nothing imports by name). Read the target file's own module docstring before
    deleting anything that looks unreferenced; a generated-artifact docstring says so explicitly.
    This exact deletion regressed twice in one day (task-8850, then task-9055 re-deleted what
    task-8850 had just restored) — CI's pytest stage catches it, but only after the commit lands,
    so treat "looks like dead code, nothing imports it" as insufficient justification for deleting
    anything under a `generated/` directory; regenerate via the sibling `*_generate_*.py` script
    instead, and check its docstring for the rationale first.
12. Adding D3 evidence (negative/failure-injection/perf/adversarial/replay tests, §5) to a test or
    foundation file that is already near 500 lines, then only noticing the `code_ratchets`
    `loc_over_500` regression after the commit — the same handful of near-threshold files
    (`test_registry.py`, `test_queries.py`, reconciliation lifecycle tests, ...) regressed this way
    three separate times in 24h (task-8627, task-8905, task-9010), each fixed by condensing
    Korean rationale prose that a prior DEEPEN pass had already written instead of referencing it.
    The check script/baseline are not the defect here: `--update` requiring an explicit call before
    a decrease is persisted is intentional (a ratchet that silently rewrites itself on every green
    run would hide a shrink nobody reviewed), and the 500/800/1000 thresholds are ADR-2026-09-10-C
    §7 observation aids, not something to raise (DECISION_GUIDELINES B-2, task-3936). Before adding
    D3 evidence to a file, check its current line count first — if it is already past ~400 lines,
    split it into a sibling `_<aspect>.py` test file by responsibility before adding evidence,
    instead of adding first and trimming docstrings after `code_ratchets` goes red. Same applies to
    a new intentional fail-closed `raise NotImplementedError` stub: add the `# ratchet-allow:
    <reason>` comment (§3) in the same commit that introduces it, not as a follow-up fix.
13. Re-diagnosing a `journeys` (Playwright J1-J3) red from scratch without first checking whether
    the assigned worktree is already synced past the commit that fixed it — `frontend/playwright.
    config.ts` accumulated four independent root-cause fixes in one day (task-8572 vite dev JIT
    contention, task-8753 cross-worktree port collision, task-8952 redundant `tsc -b` in the e2e
    build, task-9054 webServer overrun from unbounded Playwright worker count) and each one was a
    real defect at the time. But task-8931 then reported the same `Timed out waiting 180000ms`
    symptom again, and its actual finding was that the assigned worktree just hadn't pulled
    origin/main yet — task-8952 and a later fix were already merged, the suite was green as soon as
    the worktree synced (27 passed/1 skipped, ~26s locally), and no source change was needed. The
    ND-17 retry of that same leaf (task-9068) then burned its full turn budget re-running the same
    bisect on a still-stale checkout and died with `error_max_turns` before it could commit or even
    reach that conclusion, which is what pushed the 24h repeat counter over the systemic threshold
    (task-9124). The check script and thresholds were not the defect either time. Before bisecting
    a `journeys` red: run `git -C <worktree> log --oneline -5 -- frontend/playwright.config.ts` and
    `git status` first to confirm the worktree isn't simply behind an already-landed fix, and try a
    plain re-run (`npm run build:e2e --workspace=apps/web && npx playwright test journey-j1
    journey-j2 journey-j3 --project=chromium`) before spending turns on a fresh bisect.
14. Treating every `type_ignore` (PLT-40 `check_type_ignore_budget.py`) red as a script defect —
    task-8993 flagged 11 correction leaves in 24h (task-8920, 9014, 9070, 9113, 9136, ...) and the
    ratchet mechanism itself is sound (task-9140 reverified: 142/142, ~8s runtime, well under the
    180s gate timeout). The 11 leaves are two unrelated failure classes wearing the same gate name:
    (a) a real perf bug in `_iter_python_files` (rglob walked into `.mypy_cache`/`.hypothesis`
    before filtering, 180s timeout) — root-caused once in task-9014, patched again in task-9113 for
    a missed `.import_linter_cache` entry, and done since; task-9070/9136 then re-reported the same
    180s symptom from worktrees that simply hadn't pulled the fix yet — the journeys pattern (#13)
    repeating under a different gate name. (b) genuine budget increases (task-8891, 8920) from D2/D3
    negative-test leaves (§5) that inject a wrong-typed value into a typed function/dataclass to
    prove fail-closed behavior — mypy then requires `# type: ignore` on that exact injection line,
    which the ratchet (correctly) counts as a regression. That is a structural collision between two
    enforced policies (D2/D3 negative-test mandate vs. "budget never grows"), not a bug in either
    check; task-8891's fix demonstrates the workaround (build the invalid payload as
    `dict[str, Any]` and `**`-unpack it so mypy widens to `object` and needs no ignore) but there is
    no way to eliminate the underlying tension in general. Before filing a new `type_ignore`
    correction leaf: confirm the worktree is synced past the latest fix on
    `scripts/check_type_ignore_budget.py`/`type-ignore-budget.txt` first: if the symptom is a 180s
    timeout, `git log --oneline -5 -- scripts/check_type_ignore_budget.py` and a plain re-run settle
    it; if it's a budget increase, look for a D2/D3 negative-test leaf in the same window before
    assuming a fresh design defect, and prefer the `dict[str, Any]` unpack pattern over adding a new
    ignore. Budget/threshold relief is still forbidden either way (DECISION_GUIDELINES B-2).

## 7. File policy (ADR-2026-09-10-C)

Split files by bounded context / aggregate / invariant ownership, not by line count. Thresholds
are an observation aid, not the goal: 500 lines is a warn (reviewer checks for mixed
responsibilities), 800 triggers an architecture-review question (two responsibilities? independent
change axis? public/private split? testable in isolation? fan-out?), 1,000 is a hard cap unless
the file opens with `# loc-allow: <reason>` (generated tables, protocol mappings, deterministic
rule matrices). Never split a safety invariant across files just to satisfy a line count, and never
hide domain authority inside `utils.py` / `helpers.py` / `common.py`. Do not merge existing files
into a large one to "fix" line count either — only consolidate a genuinely artificial split when a
task is already modifying that domain.

## 8. Non-normative docs

`docs/ideabank/**`, `docs/blue_team/**`, `docs/red_team/**` are READ FOR CONTEXT ONLY
(ADR-2026-09-10-C Decision 7). Never cite them as the basis for an implementation decision — only
`docs/specs/L4_*.md`, `docs/design/*ADR*`, and `docs/design/INVARIANTS.md` are normative.
