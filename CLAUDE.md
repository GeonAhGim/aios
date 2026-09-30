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
    at least six times across 24h (task-8627, task-8905, task-9010, task-9069, plus the two this
    note originally cited) — telling workers in prose to "check line count first" (task-9118) did
    not stop the recurrence, because nothing made that check quick enough to actually run before
    every edit. The check script/baseline were never the defect: `--update` requiring an explicit
    call before a decrease is persisted is intentional (a ratchet that silently rewrites itself on
    every green run would hide a shrink nobody reviewed), and the 500/800/1000 thresholds are
    ADR-2026-09-10-C §7 observation aids, not something to raise (DECISION_GUIDELINES B-2,
    task-3936). task-9145 added `scripts/check_code_ratchets.py --near 30` (advisory only, does not
    touch the baseline or exit code) — run it before adding D3 evidence anywhere under `tests/` or
    `src/`; it lists every file within 30 lines of a 500/800/1000 crossing so the split happens
    before the commit, not as a follow-up red-gate fix. If a file it flags is already past ~400
    lines, split it into a sibling `_<aspect>.py` test file by responsibility before adding
    evidence. Same applies to a new intentional fail-closed `raise NotImplementedError` stub: add
    the `# ratchet-allow: <reason>` comment (§3) in the same commit that introduces it, not as a
    follow-up fix.
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
15. Treating every `ruff` `[health:ci_red]` correction leaf as a fresh violation to hunt down —
    task-8993 flagged the `ruff` stage at 4+ leaves in 24h (task-8718, 8766, 8799, 9157) and
    `pyproject.toml`'s `[tool.ruff]`/`per-file-ignores` config has no defect: a plain
    `ruff check src tests scripts` on a synced worktree is green (`All checks passed!`). The
    `ruff` gate name is unlike `type_ignore`/`code_ratchets` (one narrow metric each) — it's a
    single CI step wrapping ~15 active rule categories (F401, E501, S1xx, BLE001, ARG00x, TID251,
    PLW1510, ...) across the entire `src/`+`tests/`+`scripts/` tree, so unrelated one-line
    regressions from otherwise-unrelated leaves land under the same stage name and look like
    "repetition" in the 24h counter even when no two leaves touch the same rule or file. Three of
    the four sampled leaves (task-8718, 8766, and 9157) turned out to already be fixed by an
    earlier commit by the time they were picked up — task-8718's own note confirms the unused
    `datetime` import in `tests/foundation/integration/ems/__init__.py` was gone as of
    `7a857ce3` (task-8490) before task-8718 started; task-9157's flagged line
    (`ingest_candles.py:189`, E501 103>100) was already rewrapped by task-9056's commit
    `50c6a348` before task-9157 was assigned — the same journeys-style (#13) stale-escalation
    pattern, just under the `ruff` gate name instead of Playwright's. task-8799's recorded commit
    (`2d1c48bd6201`) is also a mismatch — that hash is actually task-8758's unrelated
    `type_ignore` fix, not an F401 fix, meaning the task's `commit` field was filled from a stale
    HEAD at push time rather than the leaf's own (empty) diff; harmless here since the note
    correctly says "커밋/푸시 불필요", but a reminder that `commit` on a noop-done ruff leaf isn't
    proof of a matching source change. Before filing or working a new `ruff` correction leaf: run
    `ruff check <the exact path:line from the esc detail>` first — if it's already clean, the
    worktree (or the escalation) is just behind an already-landed fix, and the leaf should close
    as `noop` with that ancestor commit cited, not re-fix code that's already fixed. No baseline/
    rule relief made (DECISION_GUIDELINES B-2) — none was warranted.
16. Treating every `[health:ci_red]` frontend leaf as a defect in the named test file it cites —
    task-8993 flagged `frontend` at 4+ correction leaves in 24h (task-9053/9089 both titled
    "AccountDeletionPage ... 화이트리스트 등록 에러 표시", task-9283 "SellStrategyPage ... 리스팅
    생성 에러 표시", plus earlier WriteReviewPage/DisputeSubmitPage/AdminApprovalRequestPage/
    ApprovalSettingsPage instances recorded in `esc-ci-frontend.json`) and none of the cited test
    files had a real defect — task-9053 closed noop ("이미 fixed upstream"), task-9089 closed noop
    ("cto 결정 ... noop done"), task-9283 died mid-run without finding anything to fix. Root cause
    is upstream of any file this repo owns: `pm/local_ci.py`'s `frontend` step runs plain `npm
    test` under a wall-clock subprocess timeout; under the same shared-host contention already
    documented for this suite (task-1968, task-2479, task-8950), the whole vitest run occasionally
    fails to finish inside that budget and gets killed (`rc=124`), and only a truncated tail of
    partial output survives. `pm/auto_decision.py`'s `_stage_tail()`/`_FAIL_LINE_MARKERS` then
    picks "the line that explains the failure" by a bare substring match (`"FAIL"`, `"Error"`,
    `"error"`, ...) with no case for `rc=124`/`timeout <n>s` — so the one line that actually
    explains what happened gets filtered out (it matches no marker), and the generic `"error"`
    marker instead matches whichever `stderr | <file>.test.tsx > ... > negative: ... error_code...`
    diagnostic passthrough line happened to be printed last before the kill. Those `stderr | ...`
    lines are normal Vitest console passthrough from the app's own error-handling code during
    *passing* D2/D3 negative tests (this repo's own DoD, §5, mandates ≥3 negative tests per leaf,
    and their describe/test names routinely contain the literal substring `error` via identifiers
    like `error_code`) — they carry no information about which test, if any, actually failed.
    `_build_ci_fix_leaf()` then titles the leaf off line 1 of that misleading tail, so every
    timeout picks a different, innocent test file as "the culprit" (confirmed in task-9053's own
    spec: its bisect step even attached unrelated backend Python commit candidates touching
    `tests/foundation/unit/market_data/test_backfill_job.py` etc. to a frontend vitest timeout —
    the same garbage-in classification cascading into bisect). This is a classification bug in
    `pm/auto_decision.py` (`_stage_tail`/`_FAIL_LINE_MARKERS`/`_build_ci_fix_leaf`), which is fleet
    code under `C:\aios\pm` — out of a repo worker's edit scope (§4 prohibits touching it directly;
    a fix needs an ops task with the exact diff: add a `timeout`/`rc=124` marker checked *before*
    the generic `error` substring, and stop matching bare `stderr | ... > ...` passthrough lines as
    fail evidence). Before working a new `frontend` correction leaf whose title looks like
    `stderr | <file>.test.tsx > ... > negative: ...`: run that exact test file alone
    (`npm run test --workspace=apps/web -- <file>`) first — if it's green, the leaf is this
    misclassification pattern, not a real defect; close it noop and cite this entry rather than
    reinvestigating the same innocent file again. No baseline/threshold/marker-list relief made
    from this leaf (DECISION_GUIDELINES B-2) — the fix belongs to fleet code, not this repo.
17. Re-bisecting a `type_ignore` escalation that has already been reverified green — task-8993's
    counter reached 12 leaves in 24h for this stage (task-8920, 9014, 9070, 9113, 9136, 9140, 9255,
    ...) and task-9140 (the 11th-repeat systemic investigation) already reverified the ratchet
    itself sound (142/142, ~8s) and filed the two-failure-class breakdown that is now #14 above.
    task-9261 (the 12th-repeat systemic leaf) re-ran that same investigation and found the design
    still sound (142/142, ~18s) — the new evidence is in `esc-ci-type_ignore.json` itself:
    task-9014, task-9136, and task-9255 were all created by the fleet's `ci_red` auto-action off
    the *same* `detail_hash: "42ef1e7acc80"` and the same original bisect culprit
    (`deacc374b0ed34d59cf2e4e0401ee053bd01127a`) recorded on 2026-09-22 — task-9255's own
    noop_reason concluded that commit was never the actual regression, just a worktree that was one
    `git pull` behind. task-9255 was created at 05:57, a full hour *after* task-9140's fix/doc
    commit (`29cf3622`, 04:41) had already landed and been reverified green — so the repeat is not
    new violations reaching the gate, it is the escalation/orchestrator `ci_red` rule creating (or
    reusing, see the `"reused open fix task-9070"` action) another fix leaf off the stored
    escalation record without re-running the stage's own check at current HEAD first. This is the
    same class of defect as #13 (journeys)/#15 (ruff)/#16 (frontend): fleet code under `C:\aios\pm`
    (`pm/auto_decision.py` / `orchestrator.py`'s `ci_red` rule) re-triggering a leaf from stale
    state, out of a repo worker's edit scope (§4). Before working a new `type_ignore` leaf: run
    `python scripts/check_type_ignore_budget.py` locally first (a few seconds) — if it prints `OK`,
    close the leaf noop citing this entry and task-9140/task-9255 rather than re-bisecting the same
    already-resolved `deacc374` commit again. No baseline/threshold relief made (DECISION_GUIDELINES
    B-2) — the fix belongs to fleet code (re-run the check at current HEAD before opening/reusing a
    `ci_red` fix task), not this repo.

17. Treating a new `import_linter` `[health:ci_red]`/`[health:ci_red_systemic]` correction
    leaf as still-unfixed code every time — task-8993 flagged `import_linter` at 4+ leaves in
    24h (task-8606, task-8752, task-8848, task-8930) and each one *was* a real, correctly
    diagnosed fix at the time: task-8752 parallelized `build_graph()`'s serial
    `path.read_text()` with a `ThreadPoolExecutor` (root cause: cold-checkout/fleet disk
    contention pushing the scan past the step's 120s subprocess timeout, same class as
    `check_code_ratchets.py`'s task-638dca50 fix), task-8930 tuned `SCAN_WORKERS` 16->24,
    task-9114 added `test_build_graph_overlaps_io_bound_reads` (the first local test that
    actually exercises overlapped I/O instead of tmp_path's page-cache-resident reads), and
    task-9259 reverted the default to 16 plus an `AIOS_CI_SCAN_WORKERS` env override so ops
    can retune without a new commit. None of those leaves were wasted or wrong. But
    escalation `esc-ci-import_linter.json` kept logging `"3x-repeat CI red"` every ~15-20 min
    through 2026-09-30T06:45:57Z — 8 minutes *after* task-9259's fix (commit `57963866`)
    landed at 06:37:04Z — while the very next polled CI run (sha `28561950`, started
    06:31:24Z, before 9259 even landed) shows `import_linter` green in 1.29s, and a local
    warm-cache run of the script takes ~1-2.5s. That combination (green one poll, red the
    next, same code, order-of-magnitude margin under the 120s budget when it does pass) is
    the signature of intermittent fleet-wide disk/AV contention across concurrent CI worker
    lanes, not a defect in this script's own I/O parallelization — SCAN_WORKERS controls only
    this one lane's own thread pool, it cannot see or compensate for sibling lanes on the same
    host saturating disk/AV at the same moment. The actual lever left is the flat 120s
    subprocess timeout hardcoded per-step in `pm/ci_recheck.py`'s `build_steps` (shared by
    `complexity` and `import_linter`, both single-lane AST/graph scanners of `src/`) — that is
    fleet code (`C:\aios\pm`), out of a repo worker's edit scope (§4); a fix needs an ops task
    with the exact diff (e.g. raise the timeout for these two steps, or throttle concurrent
    CI lane count) rather than another `scripts/check_import_linter.py` tuning pass. Before
    filing or working a new `import_linter` correction leaf: run the script locally
    (`python scripts/check_import_linter.py`) first — if it's green and fast, and
    `ci/<sha>.json` around the flagged time shows it passing on at least one poll, the leaf is
    this intermittent-contention pattern, not a fresh code defect; close it noop citing this
    entry instead of re-tuning `SCAN_WORKERS` again. No baseline/timeout relief made from this
    leaf (DECISION_GUIDELINES B-2) — the fix belongs to fleet code, not this repo.

18. Treating every `complexity` `[health:ci_red_systemic]` correction leaf as still-unfixed
    code — task-8993 flagged `complexity` at 4+ leaves in 24h (task-8653, task-8747, task-8844,
    task-8887) and `scripts/check_complexity.py`/`complexity-baseline.json` have no design
    defect: a plain `python scripts/check_complexity.py` on a synced worktree prints
    `OK: {'over_cap_count': 9} (baseline {'over_cap_count': 9}, CAP=25)` — the ratchet, the CAP,
    and the ast-based scorer all behave as specified. task-8653 was the real, correctly
    diagnosed fix (split `okx_endpoint_coverage.py`'s `_pairs_from_file()` to bring
    `over_cap_count` back to baseline, commit `098df234`, landed 2026-09-29T07:51:12Z). Every
    leaf after that was the same journeys/ruff/frontend/type_ignore pattern (#13/#15/#16/#17)
    repeating under the `complexity` gate name: task-8747's own note already says so verbatim
    ("현재 HEAD/origin/main에서 게이트 green, 회귀 이미 해소됨", 09:38:19Z, commit `e5f5df7b`),
    yet `esc-ci-complexity.json` kept reusing the same `detail_hash` (`8ec0d05e7688`, itself
    from `sha 41c47310`) to spawn task-8844 (12:10Z) and task-8887 (14:23Z) — both closed noop,
    both commit `none`/`None` — and then logged `"3x-repeat CI red"` on the *same* stale
    escalation every ~15-30 min for the next ~17 hours (14:43Z through 07:07Z the next day)
    without ever re-running the check at current HEAD or creating a further fix task. This is
    the same class of defect as #13 (journeys) / #15 (ruff) / #16 (frontend) / #17
    (type_ignore): fleet code under `C:\aios\pm` (`pm/auto_decision.py` / `orchestrator.py`'s
    `ci_red` rule) re-triggering off a stale escalation record instead of re-checking current
    HEAD, out of a repo worker's edit scope (§4). Before filing or working a new `complexity`
    correction leaf: run `python scripts/check_complexity.py` locally first (a couple seconds)
    — if it prints `OK` and matches baseline, close the leaf noop citing this entry and
    task-8747 rather than re-diagnosing already-fixed violations. No baseline/CAP relief made
    from this leaf (DECISION_GUIDELINES B-2) — the fix belongs to fleet code (re-run the check
    at current HEAD before opening/reusing a `ci_red` fix task off `esc-ci-complexity.json`),
    not this repo.

19. Re-running a `perf_marker_guard` systemic investigation that a prior systemic leaf already
    closed with the right root cause — task-8993 flagged this stage at 10+ leaves in 24h
    (task-8849, 8889, 8932, 9135, 9254, plus the two systemic leaves) and the first systemic
    leaf, task-9138, already found the real cause: the AST scan and the 60s budget (tuned in
    task-9135's `_test_files`/substring-prefilter fix, `scripts/check_perf_marker_guard.py:121-151`)
    are both correct — the repeated correction leaves were unrelated new tests each adding a
    wall-clock `perf_counter()`/`monotonic()` assert without `@pytest.mark.perf`, because
    `docs/TESTING.md` had no section telling authors the marker was required until task-9138
    added one (commit `a2cafe7b`, "성능(wall-clock) 예산 테스트 작성 규칙"). That fix was correct
    and is still in place — a local run confirms `OK` in ~2s, an order of magnitude under the
    60s budget. But `esc-ci-perf_marker_guard.json` kept the escalation open after task-9135's
    fix landed (`03:13:11Z`) and re-created fix task-9254 at `05:57:08Z` off the *same*
    `detail_hash: "dbd3aa2db6c7"` — task-9254 confirmed noop (already fixed, stale timeout
    escalation), yet the escalation still spawned a second `ci_red_systemic` leaf (this one,
    task-9289) afterward instead of recognizing task-9138 had already closed the systemic
    question with a landed doc fix. This is the same fleet-code defect as #17
    (`pm/auto_decision.py`/`orchestrator.py`'s `ci_red` rule reusing a stale escalation record
    instead of re-checking current HEAD and prior systemic-leaf resolution before opening
    another leaf) applied to a second gate. Before working a new `perf_marker_guard` leaf
    (individual or systemic): run `python scripts/check_perf_marker_guard.py` locally first — if
    it's `OK` and fast, and a systemic leaf already exists with a landed fix commit in its note,
    close as noop citing that leaf and this entry rather than re-investigating the same design.
    No baseline/timeout relief made (DECISION_GUIDELINES B-2) — script/docs design is sound; the
    remaining defect is fleet code, not this repo.

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
