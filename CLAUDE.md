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
11. Appending CI / systemic-investigation write-ups to this file. CLAUDE.md is auto-loaded into
   every worker's context: on 2026-09-30 it grew 8KB -> 199KB and every local-lane request
   blew the 41k-token budget. Record such findings in `docs/CI_SYSTEMIC_LOG.md` (append-only)
   and keep this list to short, general rules only.

87. A ninth `consistency` 24h 5-repeat systemic leaf (task-9759) citing the identical repeat
    set #22 (task-9011/9281/9460), #31 (task-9301/9460), #35 (task-9537), #54 (task-9620), #61
    (task-9665), #71 (task-9691), #78 (task-9595), and #80 (task-9718) already closed eight
    times — task-9759's spec names task-9011, task-9281, task-9460, task-9574 verbatim, the
    same set every prior systemic leaf already root-caused. `scripts/consistency/*`/
    `consistency-baseline.json` are unchanged since task-9122's fix (commit `693fa98a8`,
    deduping `check_port_protocol_implementations`'s redundant double `ast.walk()` via
    `common.py`'s cached `_walked_nodes(path)`, plus a structural AST-count regression-guard
    test) — `git log --oneline -3 -- scripts/consistency/ scripts/check_consistency.py
    consistency-baseline.json` confirms `693fa98a8` as the latest touch, no commits since. A
    local run on this worktree (`git status` clean) confirms `OK` and matches every one of the
    13 tracked metrics in `consistency-baseline.json` exactly (`router_unregistered=0
    port_method_unimplemented=0 port_protocol_unimplemented=0 env_key_undocumented=4
    feature_flag_undocumented=0 event_type_unconsumed=4 migration_hygiene=0
    openapi_client_mismatch=34 spec_leaf_untraced=32 naive_datetime=0 money_float=0
    symbol_id_assembly=1 spec_template_incomplete=0 authority_duplication=4`).
    `esc-ci-consistency.json` itself confirms the mechanism directly: `status: "resolved"`,
    `resolved_sha: "12e7bd738c392c2bd8b5c8dd0af15dcdd06c15b6"`, `closed_at:
    "2026-09-30T00:53:50+00:00"`, `bisect.bisect_culprit:
    "4d5ebed5b621a5e92c18eca2aa8d654e195577d8"` (the same docstring-only translation commit,
    task-4424, #21/#22/#31/#35/#54/#61/#71/#78/#80 already named as unrelated to this gate's
    logic) — with `auto_actions` showing "fix task-9011 done — 다음 CI 평가 대기" repeated many
    times after the resolution, the same fleet-code pattern already named for nineteen+ gates
    (#17-#26, #30-#31, #33-#36, #38, #40, #48, #50, #56, #59, #61, #71, #78, #80): `pm/
    auto_decision.py`/`orchestrator.py`'s `ci_red` rule not checking `status == "resolved"`
    before appending further repeat-count entries or reusing the escalation for a new systemic
    leaf, out of a repo worker's edit scope (§4). No script/baseline change made — task-9122's
    fix is still in place and sufficient. Before working a future `consistency` leaf: run
    `python scripts/check_consistency.py` locally first — if `OK` and baseline-matching, and the
    escalation's `bisect_culprit` is `4d5ebed5` (or cites an already-closed leaf verbatim), close
    as noop citing task-9122, #22/#31/#35/#54/#61/#71/#78/#80, and this entry rather than
    re-diagnosing a tenth time. No baseline/threshold relief made (DECISION_GUIDELINES B-2) —
    script/baseline design is sound; the remaining defect is fleet code, not this repo.

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
