#!/bin/bash
# AIOS — Claude Code on the web(클라우드 세션) 부트스트랩.
#
# 클라우드 컨테이너는 세션마다 새로 만들어지므로(디스크 상태는 훅 완료 시점에
# 캐시됨) 로컬 개발 머신의 수동 셋업(README.md "시작")을 이 훅이 대신한다.
#   1. PostgreSQL 16 로컬 클러스터 기동 + docker-compose.dev.yml과 같은
#      role(user/password)·DB(aios_dev) 보장  — 컨테이너에 docker 데몬이 없다.
#   2. .venv(Python 3.11) + `pip install -e ".[test,dev]"` (pyproject 해시로 재설치 생략)
#   3. .env 생성(.env.example 복사) + 로컬 전용 JWT/암호화 키 자동 생성
#   4. alembic upgrade head (aios_dev)
#   5. 세션 전용 테스트 DB aios_test_cloud 생성·마이그레이션 → TEST_DATABASE_URL export
#   6. frontend/ `npm ci --workspaces --include-workspace-root` (lockfile 해시로 생략)
#   7. PATH에 .venv/bin 추가
# 멱등(idempotent)·비대화식이며, CLAUDE_CODE_REMOTE=true 가 아니면 아무것도 하지 않는다.
# 로컬 Windows/macOS 개발 머신에는 영향이 없다.
set -euo pipefail

if [ "${CLAUDE_CODE_REMOTE:-}" != "true" ]; then
  exit 0
fi

ROOT="${CLAUDE_PROJECT_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)}"
cd "$ROOT"
ENV_FILE="${CLAUDE_ENV_FILE:-/dev/null}"
PY="${AIOS_PYTHON:-python3.11}"
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
  # docker-compose.dev.yml: POSTGRES_USER=user / POSTGRES_PASSWORD=password / POSTGRES_DB=aios_dev
  # (테스트·마이그레이션이 CREATE ROLE/EXTENSION/DATABASE를 하므로 superuser — 로컬 전용)
  if [ "$(su postgres -c "psql -Atc \"SELECT 1 FROM pg_roles WHERE rolname='user'\"")" != "1" ]; then
    log "creating role user"
    su postgres -c "psql -qc \"CREATE ROLE \\\"user\\\" LOGIN SUPERUSER PASSWORD 'password'\""
  fi
  if [ "$(su postgres -c "psql -Atc \"SELECT 1 FROM pg_database WHERE datname='aios_dev'\"")" != "1" ]; then
    log "creating database aios_dev"
    su postgres -c "createdb -O user aios_dev"
  fi
else
  log "WARNING: pg_lsclusters not found — PostgreSQL must be provided some other way (DATABASE_URL)"
fi

# ---------------------------------------------------------------- 2. Python venv
if [ ! -x "$VENV/bin/python" ]; then
  log "creating venv with $PY"
  "$PY" -m venv "$VENV"
  "$VENV/bin/pip" install -q --upgrade pip setuptools wheel
fi
PYPROJECT_SHA=$(sha256sum pyproject.toml | cut -c1-64)
if [ "$(cat "$VENV/.pyproject.sha256" 2>/dev/null || true)" != "$PYPROJECT_SHA" ]; then
  log "pip install -e \".[test,dev]\" (pyproject changed or first run)"
  "$VENV/bin/pip" install -q -e ".[test,dev]"
  printf '%s' "$PYPROJECT_SHA" > "$VENV/.pyproject.sha256"
else
  log "python deps up to date"
fi

# ---------------------------------------------------------------- 3. .env
if [ ! -f .env ]; then
  log "creating .env from .env.example"
  cp .env.example .env
fi
fill_if_empty() {
  # 값이 비어 있는(주석만 있는) 키에만 로컬 난수 키를 채운다 — 이미 값이 있으면 건드리지 않는다.
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
  LOCK_SHA=$(sha256sum frontend/package-lock.json | cut -c1-64)
  if [ ! -d frontend/node_modules ] || [ "$(cat frontend/node_modules/.lock.sha256 2>/dev/null || true)" != "$LOCK_SHA" ]; then
    log "npm ci (frontend workspaces)"
    (cd frontend && npm ci --no-audit --no-fund --workspaces --include-workspace-root)
    printf '%s' "$LOCK_SHA" > frontend/node_modules/.lock.sha256
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

log "done in $(( $(date +%s) - started ))s — python $("$VENV/bin/python" --version 2>&1 | cut -d' ' -f2), node $(node --version), postgres $(su postgres -c "psql -Atc 'show server_version'")"
log "$TEST_URL_LINE"
