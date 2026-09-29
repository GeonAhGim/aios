# Cloud session setup -- developing AIOS in Claude Code on the web

Some development on this repository happens in Claude Code on the web (cloud) sessions
instead of the Windows fleet machine described in `README.md`. Each cloud session
container is created fresh, so this document is the single source for what gets
provisioned automatically, what still requires a human, and how that differs from the
local Windows workflow. The local Windows steps in `README.md` "Getting started" and
`docs/TESTING.md` remain valid as written; nothing here changes them.

## Why a SessionStart hook instead of `.devcontainer`

Claude Code on the web supports `.devcontainer`-based images, but this repository's
dev dependencies (a local PostgreSQL 16 role/database matching
`docker-compose.dev.yml`, generated JWT/encryption keys, `alembic upgrade head`, a
session-scoped test database) are stateful setup steps, not just installed packages --
they need to run again, idempotently, against whatever is already on disk in a resumed
or cached session, not only once at image build time. A `SessionStart` hook runs on
every session start (including resumes), so it can detect and skip already-satisfied
steps (unchanged lock file, existing venv, running Postgres cluster) instead of
re-provisioning from scratch, which a devcontainer's one-shot build step cannot do as
naturally.

## 1. Measured container environment (2026-09-26, session container)

| Item | Value |
|---|---|
| OS | Ubuntu 24.04.4 LTS, x86_64, container, running as root |
| CPU / RAM | 4 vCPU / 15 GiB |
| Disk | session write allowance well above what this repo's venv + node_modules need |
| Python | 3.10.20, 3.11.15, and 3.12.3 are all present; `python3` resolves to 3.11.15 |
| Node / npm | 22.22.2 / 10.9.7 |
| PostgreSQL | 16.13 installed (`pg_ctlcluster 16 main`), not started by default |
| Docker | client present, no daemon reachable -- `docker compose` cannot start Postgres here |
| Persistence | the container is reclaimed after the session ends; anything not committed and pushed is lost |

## 2. Automatic bootstrap -- `.claude/hooks/session-start.sh`

`.claude/settings.json` registers this script as a `SessionStart` hook (synchronous,
900s timeout). The very first line checks `CLAUDE_CODE_REMOTE == "true"` and exits
immediately otherwise, so the script has no effect on the local Windows fleet
(`tests/unit/scripts/test_session_start_hook.py` pins this as a no-op). The remaining
steps are idempotent -- running the hook again with nothing changed is a fast no-op.

| Step | What it does | Why |
|---|---|---|
| 1 | Start the PostgreSQL 16 cluster; ensure role `user`/`password` (superuser, local only) and database `aios_dev` exist | matches `docker-compose.dev.yml`, which cannot run without a docker daemon here |
| 2 | Create `.venv`, install `requirements-lock.txt` + the package itself -- skipped once the lock file and `pyproject.toml` are unchanged | `docs/DEPENDENCIES.md` |
| 3 | Create `.env` from `.env.example` if missing; fill `JWT_SECRET_KEY` and `CREDENTIAL_ENCRYPTION_KEY` with locally generated random values only when those keys are empty. Exchange credentials (`BITGET_*`/`KIS_*`/`NH_*`) are never touched -- they stay blank | `src/core/loader/secret_loader.py`; no real credential is ever generated or required |
| 4 | `alembic upgrade head` against `aios_dev` | `README.md` |
| 5 | Create/migrate a session test database via `scripts/setup_test_db.py cloud --print-env` and export `TEST_DATABASE_URL` | `docs/TESTING.md`, `scripts/setup_test_db.py` |
| 6 | `frontend/` `npm ci --workspaces --include-workspace-root` -- skipped once `package-lock.json` is unchanged | `frontend/package.json` workspaces |
| 7 | Add `.venv/bin` to `PATH`, export `VIRTUAL_ENV` and `TEST_DATABASE_URL` for the rest of the session | |

### `requirements-lock.txt` and `backports.asyncio.runner`

`requirements-lock.txt` was frozen from a Python 3.10 venv (`docs/DEPENDENCIES.md`) and
pins `backports.asyncio.runner==1.2.0` unconditionally. That package has no wheel for
Python 3.11 or later (its own metadata restricts it to `<3.11`), so
`pip install -r requirements-lock.txt` aborts outright on a 3.11+ interpreter with "no
matching distribution found". The hook checks the venv's own interpreter version and
drops that one line from the install when it is 3.11 or newer, where the standard
library already provides `asyncio.Runner` and the backport is not needed.

## 3. Running the CI-equivalent gates in a session

```bash
ruff check src tests scripts
mypy src
python scripts/check_zone_manifest.py
python scripts/check_code_language.py
python scripts/check_code_ratchets.py
python scripts/check_type_ignore_budget.py
python scripts/check_baseline_raise.py
python scripts/check_migration_chain.py
python -m pytest <touched paths> -q --override-ini addopts=   # TEST_DATABASE_URL is exported by the hook
```

Scope `pytest` to the paths you touched; running the full suite is not part of the
normal session workflow (see `CLAUDE.md` §4).

## 4. What a human still has to provide

- **Exchange/data-source credentials**: `BITGET_*`, `KIS_*`, `NH_*`,
  `OPENDART_API_KEY`, and similar. The hook leaves these blank -- the app starts fine
  with them empty (only the corresponding adapter fails closed). Put real values in
  the session's environment configuration, never in a committed file.
  `secret_loader.py` prefers `os.environ` over `.env`.
- **`CREDENTIAL_ENCRYPTION_KEY`**: the value the hook generates is a random key for
  this session only. Restoring a database with previously encrypted credentials
  requires supplying the original key through the environment, the same way.
- Anything requiring a real GitHub/exchange/broker account is out of scope for a
  cloud session and stays human-only.

## 5. Validation record

Measured in this session, cold (no `.venv`/`.env`/`node_modules`, PostgreSQL cluster
stopped) and warm (immediately after):

| Run | Duration | Result |
|---|---|---|
| Cold (`CLAUDE_CODE_REMOTE=true`, first run) | 51.6s | PostgreSQL started, role/DB created, venv created, `requirements-lock.txt` installed with `backports.asyncio.runner` skipped, `.env` generated, `alembic upgrade head` ran to the current head, session test DB created, `frontend/` `npm ci` ran |
| Warm (immediately after, no changes) | 1.8s | every install/migrate/`npm ci` step recognized as already satisfied and skipped |
| No-op (`CLAUDE_CODE_REMOTE` unset) | immediate | exit 0, no output, no filesystem changes -- `tests/unit/scripts/test_session_start_hook.py` |

Test evidence against the hook-started PostgreSQL (`TEST_DATABASE_URL` as exported by
the hook):

- `tests/unit/scripts/test_session_start_hook.py` -- 4 passed
- `tests/integration/api/test_health_endpoints.py` (real Postgres) -- 14 passed
