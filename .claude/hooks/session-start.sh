#!/bin/bash
# AIOS -- SessionStart hook for Claude Code on the web (cloud sessions).
#
# A cloud session container is created fresh, so this hook automates the
# manual local setup documented in README.md's "Getting started" section:
#   1. Start local PostgreSQL 16 and ensure the role/DB from
#      docker-compose.dev.yml exist (the container has no docker daemon).
#   2. Create .venv and install requirements-lock.txt + the package itself
#      (skipped once the lock file and pyproject.toml are unchanged).
#   3. Create .env from .env.example with locally generated JWT/encryption
#      keys. Exchange credentials (BITGET_*/KIS_*/NH_*) are left blank --
#      see docs/CLOUD_SESSION_SETUP.md.
#   4. alembic upgrade head against aios_dev.
#   5. Create/migrate a session-scoped test database and export
#      TEST_DATABASE_URL.
#   6. frontend/ npm ci (skipped once package-lock.json is unchanged).
#   7. Add .venv/bin to PATH for the rest of the session.
#
# Idempotent and non-interactive. This is a no-op unless CLAUDE_CODE_REMOTE
# is exactly "true", so the local Windows fleet is unaffected -- see
# tests/unit/scripts/test_session_start_hook.py.
set -euo pipefail

if [ "${CLAUDE_CODE_REMOTE:-}" != "true" ]; then
  exit 0
fi

ROOT="${CLAUDE_PROJECT_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)}"
cd "$ROOT"
ENV_FILE="${CLAUDE_ENV_FILE:-/dev/null}"
VENV="$ROOT/.venv"
PG_VER=16
TEST_DB_SESSION="${AIOS_TEST_DB_SESSION:-cloud}"

log() { printf '[session-start] %s\n' "$*"; }
started=$(date +%s)

# ---------------------------------------------------------------- 1. PostgreSQL
if command -v pg_lsclusters >/dev/null 2>&1; then
  if ! pg_lsclusters 2>/dev/null | awk -v v="$PG_VER" '$1==v && $2=="main" && $4=="online"{ok=1} END{exit !ok}'; then
    log "starting PostgreSQL $PG_VER cluster"
    pg_ctlcluster "$PG_VER" main start 2>/dev/null || service postgresql start
  fi
  # docker-compose.dev.yml: POSTGRES_USER=user / POSTGRES_PASSWORD=password / POSTGRES_DB=aios_dev.
  # Superuser because tests/migrations issue CREATE ROLE/EXTENSION/DATABASE -- local dev only.
  if [ "$(su postgres -c "psql -Atc \"SELECT 1 FROM pg_roles WHERE rolname='user'\"")" != "1" ]; then
    log "creating role user"
    su postgres -c "psql -qc \"CREATE ROLE \\\"user\\\" LOGIN SUPERUSER PASSWORD 'password'\""
  fi
  if [ "$(su postgres -c "psql -Atc \"SELECT 1 FROM pg_database WHERE datname='aios_dev'\"")" != "1" ]; then
    log "creating database aios_dev"
    su postgres -c "createdb -O user aios_dev"
  fi
else
  log "WARNING: pg_lsclusters not found -- PostgreSQL must be provided some other way (DATABASE_URL)"
fi

# ---------------------------------------------------------------- 2. Python venv
pick_python() {
  for cand in python3 python3.12 python3.11 python3.10; do
    if command -v "$cand" >/dev/null 2>&1; then
      printf '%s' "$cand"
      return 0
    fi
  done
  return 1
}
PY="${AIOS_PYTHON:-$(pick_python)}"

if [ ! -x "$VENV/bin/python" ]; then
  log "creating venv with $PY"
  "$PY" -m venv "$VENV"
  "$VENV/bin/pip" install -q --upgrade pip setuptools wheel
fi

# requirements-lock.txt pins backports.asyncio.runner==1.2.0 (a <3.11 shim
# providing asyncio.Runner) unconditionally -- it was frozen from a 3.10 venv
# (docs/DEPENDENCIES.md) and has no matching wheel on 3.11+, which aborts the
# whole `pip install -r` with "no matching distribution found". Drop that one
# line when the venv's own interpreter is 3.11+, where asyncio.Runner is
# already in the standard library and the shim is not needed.
LOCK_SHA=$(cat requirements-lock.txt pyproject.toml | sha256sum | cut -c1-64)
if [ "$(cat "$VENV/.lock.sha256" 2>/dev/null || true)" != "$LOCK_SHA" ]; then
  log "installing requirements-lock.txt (lock file or pyproject.toml changed)"
  REQ_FILE="requirements-lock.txt"
  PY_MINOR=$("$VENV/bin/python" -c 'import sys; print(sys.version_info[1])')
  if [ "$PY_MINOR" -ge 11 ]; then
    REQ_FILE=$(mktemp)
    grep -v '^backports\.asyncio\.runner==' requirements-lock.txt > "$REQ_FILE"
    log "python 3.$PY_MINOR: skipping backports.asyncio.runner (no 3.11+ wheel; stdlib already has asyncio.Runner)"
  fi
  "$VENV/bin/pip" install -q -r "$REQ_FILE"
  [ "$REQ_FILE" = "requirements-lock.txt" ] || rm -f "$REQ_FILE"
  "$VENV/bin/pip" install -q -e . --no-deps
  printf '%s' "$LOCK_SHA" > "$VENV/.lock.sha256"
else
  log "python deps up to date"
fi

# ---------------------------------------------------------------- 3. .env
if [ ! -f .env ]; then
  log "creating .env from .env.example"
  cp .env.example .env
fi
fill_if_empty() {
  # Only fills a key whose value is empty (comment-only line) -- never
  # overwrites a value that is already set. Exchange credential keys
  # (BITGET_*/KIS_*/NH_*) are deliberately never passed here: they stay
  # blank so no real credential is ever generated or required.
  local key="$1"
  if grep -qE "^${key}=[[:space:]]*(#.*)?$" .env; then
    local val
    val=$(openssl rand -hex 32)
    sed -i -E "s|^${key}=.*|${key}=${val}|" .env
    log "generated local $key"
  fi
}
fill_if_empty JWT_SECRET_KEY
fill_if_empty CREDENTIAL_ENCRYPTION_KEY

# ---------------------------------------------------------------- 4. migrations
log "alembic upgrade head (aios_dev)"
"$VENV/bin/alembic" -q upgrade head

# ---------------------------------------------------------------- 5. session test DB
log "ensuring test database aios_test_${TEST_DB_SESSION}"
TEST_URL_LINE=$("$VENV/bin/python" scripts/setup_test_db.py "$TEST_DB_SESSION" --print-env | tail -n1)
case "$TEST_URL_LINE" in
  TEST_DATABASE_URL=*) ;;
  *) log "ERROR: unexpected setup_test_db output: $TEST_URL_LINE"; exit 1 ;;
esac

# ---------------------------------------------------------------- 6. frontend
if [ -f frontend/package-lock.json ]; then
  LOCK_SHA_FE=$(sha256sum frontend/package-lock.json | cut -c1-64)
  if [ ! -d frontend/node_modules ] || [ "$(cat frontend/node_modules/.lock.sha256 2>/dev/null || true)" != "$LOCK_SHA_FE" ]; then
    log "npm ci (frontend workspaces)"
    (cd frontend && npm ci --no-audit --no-fund --workspaces --include-workspace-root)
    printf '%s' "$LOCK_SHA_FE" > frontend/node_modules/.lock.sha256
  else
    log "frontend deps up to date"
  fi
fi

# ---------------------------------------------------------------- 7. session env
{
  echo "export PATH=\"$VENV/bin:\$PATH\""
  echo "export VIRTUAL_ENV=\"$VENV\""
  echo "export $TEST_URL_LINE"
} >> "$ENV_FILE"

log "done in $(( $(date +%s) - started ))s -- python $("$VENV/bin/python" --version 2>&1 | cut -d' ' -f2), postgres $(su postgres -c "psql -Atc 'show server_version'")"
log "$TEST_URL_LINE"
