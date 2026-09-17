# 클라우드 세션 셋업 — Claude Code 웹에서 AIOS 개발을 이어가기

2026-09-15, 개발 환경을 기존 Windows PC(`C:\aios\*`)에서 **Claude Code 웹(claude.ai/code) 클라우드
세션**으로 옮긴다. 이 문서는 그 환경에서 프로젝트를 이어가기 위해 무엇이 자동으로 준비되고, 무엇을
사람이 해야 하며, 기존 함대(Windows) 운영과 무엇이 다른지의 단일 출처다. 로컬 Windows 절차는
`README.md` "시작"과 `docs/TESTING.md`가 그대로 유효하다.

## 1. 환경 실측 (2026-09-15, 세션 컨테이너)

| 항목 | 값 |
|---|---|
| OS | Ubuntu 24.04.4 LTS, x86_64, KVM 위 컨테이너, 실행 계정 root |
| CPU / RAM | 4 vCPU (Intel Xeon 2.80GHz) / 15 GiB |
| 디스크 | 세션별 쓰기 허용량 약 30 GB (`df`의 Avail 기준) |
| Python | 3.11.15 (`python3.11`; CI `quality.yml`과 동일 메이저) |
| Node / npm | 22.22.2 / 10.9.7 (CI와 동일 메이저) |
| PostgreSQL | 16.13 로컬 클러스터 설치됨(`pg_ctlcluster 16 main`), port 5432 |
| Docker | 클라이언트만 있고 데몬 없음 → `docker compose`로 Postgres를 띄울 수 없다 |
| gh / codex / copilot / cursor-agent | 없음 — GitHub 작업은 세션의 GitHub MCP 도구로 한다 |
| 네트워크 | 아웃바운드 HTTPS는 세션 프록시 경유. pypi.org·registry.npmjs.org 직접 허용 |
| 지속성 | **컨테이너는 세션 종료·유휴 후 회수된다.** 커밋·푸시하지 않은 것은 사라진다 |

## 2. 자동 부트스트랩 — `.claude/hooks/session-start.sh`

`.claude/settings.json`의 `SessionStart` 훅(동기, timeout 900s)이 세션마다 실행된다.
`CLAUDE_CODE_REMOTE=true`일 때만 동작하므로 로컬 개발 머신에는 영향이 없다. 멱등이라 여러 번 실행해도 안전하다.

| 단계 | 하는 일 | 근거 |
|---|---|---|
| 1 | PostgreSQL 16 클러스터 기동, role `user`/`password`(superuser, 로컬 전용)와 DB `aios_dev` 보장 | `docker-compose.dev.yml`과 동일 접속 정보 |
| 2 | `.venv`(Python 3.11) 생성, `pip install -e ".[test,dev]"` — `pyproject.toml` 해시가 같으면 생략 | `quality.yml` verify job |
| 3 | `.env` 없으면 `.env.example` 복사, `JWT_SECRET_KEY`·`CREDENTIAL_ENCRYPTION_KEY`가 비어 있으면 `openssl rand -hex 32`로 로컬 키 생성 | `src/core/loader/secret_loader.py` |
| 4 | `alembic upgrade head` (aios_dev) | `README.md` |
| 5 | 세션 테스트 DB `aios_test_cloud` 생성·마이그레이션 → `TEST_DATABASE_URL` export | `docs/TESTING.md`, `scripts/setup_test_db.py` |
| 6 | `frontend/`에서 `npm ci --workspaces --include-workspace-root` — lockfile 해시가 같으면 생략 | `quality.yml` frontend job |
| 7 | `PATH`에 `.venv/bin` 추가, `VIRTUAL_ENV`·`TEST_DATABASE_URL`을 세션 환경에 export | |

실측 소요 시간: 콜드(venv·node_modules·.env·테스트 DB 없음) 33초(pip 캐시가 있는 컨테이너 기준.
완전히 새 이미지에서는 pip 다운로드 시간이 더 든다), 웜 2초. TA-Lib는 PyPI `ta-lib` 0.8.0 wheel이
C 라이브러리를 내장하므로 별도 설치가 필요 없다(0.6.5 이상).

훅이 export한 값은 세션의 셸에 이미 들어와 있으므로, 세션 안에서는 그냥 `python`, `pytest`,
`alembic`을 쓰면 된다(`.venv/Scripts/...` 접두어는 Windows 전용).

## 3. 세션에서 게이트 실행 (CI `verify`·`frontend` job과 동일)

```bash
ruff check src tests scripts
mypy src
python scripts/check_zone_manifest.py
python scripts/check_no_bom.py
python scripts/check_compliance_gate.py
python scripts/check_child_order_path.py
python scripts/check_migration_chain.py
python -m pytest -q --cov=src --cov-report=term --cov-report=xml   # TEST_DATABASE_URL은 훅이 설정
python scripts/coverage_ratchet.py
cd frontend && npm run lint --workspace=apps/web && npm run build --workspace=apps/web \
  && npm run test:coverage --workspace=apps/web && node ../scripts/frontend_coverage_ratchet.mjs
```

pytest는 4 vCPU에서 CI(약 12분)와 비슷하거나 더 걸린다. `-n`(xdist)은 `pyproject.toml`의 주석대로
아직 격리 결함이 있어 쓰지 않는다. `nightly`·`live_demo` 마커는 기본 제외다.

## 4. 기존 Windows 함대와의 차이 — 무엇이 없고 무엇으로 대체하는가

| 기존(Windows `C:\aios`) | 클라우드 세션 | 대체 |
|---|---|---|
| `C:\aios\pm\` 통제면 — `orchestrator.py`, `pm_cycle.py`, `local_ci.py`(게이트 16종), `healthcheck.py`, `dashboard.py`, `tasks/*.json`, `human_blocked.json`, `escalations/`, `mcp/` | **없음.** 어떤 리포(aios, aios-meta, AIOSproject, aios-idea-bank)에도 버전 관리돼 있지 않다. 2026-09-15 확인 | 세션 안에서 Claude가 `docs/specs/*` §9 리프를 직접 구현하고 §3 게이트를 직접 돌린다. 리프 상태의 진실은 `docs/specs/` 문서와 커밋 이력이다. `pm/`을 되살리려면 기존 PC에서 복사해 별도 리포로 올려야 한다 |
| 워커별 worktree `C:\aios\wt\<worker>`·DB `aios_test_<worker>` | 세션 = 컨테이너 1개, DB `aios_test_cloud` 1개 | 세션이 곧 격리 단위다 |
| `gh` CLI, `gh agent-task`(Copilot 레인) | 없음 | GitHub MCP 도구(PR·이슈·Actions 조회). Copilot 레인은 GitHub 웹에서만 |
| `docker compose -f docker-compose.dev.yml up -d` | 데몬 없음 | 훅 1단계의 로컬 PostgreSQL 16 |
| `.venv\Scripts\python.exe` | `.venv/bin/python` (훅이 PATH에 추가) | |
| `scripts/backup/restore_drill.py`, `scripts/observability/notify_selftest.py`의 기본 보고 경로 `C:\aios\pm\...` | 경로가 없다 | `--report-path`를 명시한다 |
| `pm/local_ci.py`의 게이트 16종 목록 | 리포에 없어 복원 불가. 문서에는 개수(11→16)와 일부 구성원만 남아 있다 | `quality.yml` + `docs/TESTING.md` 게이트(§3)를 기준으로 삼는다 |
| `.git/hooks/pre-push`(`check_migration_chain.py`) | 새 clone에는 없다 | 커밋 전 §3에서 직접 실행 |

## 5. 사람만 할 수 있는 것 (훅이 만들지 않는 것)

- **거래소·데이터소스 자격증명**: `BITGET_*`, `KIS_*`, `NH_*`, `OPENDART_API_KEY` 등. 훅은 빈 값으로 둔다(앱은
  빈 값으로도 기동한다 — 해당 어댑터만 fail-closed). 실제 값은 **저장소에 커밋하지 말고** claude.ai
  환경 설정의 환경변수로 넣는다. `secret_loader.py`는 `os.environ`을 `.env`보다 우선한다.
- **`CREDENTIAL_ENCRYPTION_KEY`**: 훅이 만드는 값은 이 세션 전용 난수다. 기존 DB(암호화된 자격증명 포함)를
  복원하려면 기존 PC의 키를 같은 방식(환경변수)으로 넣어야 한다.
- **GitHub repository variable `META_GUARDS_REF`**(guards job), `ALERT_WEBHOOK_URL`(Slack), Anthropic·Codex·Copilot·Cursor 계정.
- **`C:\aios\pm` 복구**: 기존 PC가 살아 있으면 복사해 두는 것이 유일한 방법이다.

## 6. 알려진 상태 (2026-09-15)

- `origin/main` 마지막 푸시는 2026-09-12 17:04Z(`6615621`). 기존 PC 함대는 그 이후 활동이 없다.
- GitHub Actions `Quality Gate`는 main에서 3시간마다 도는 스케줄 실행이 2026-09-13부터 계속 실패 중이다.
  `frontend`·`guards` job은 통과, `verify` job의 `Test` 스텝만 실패한다. 원인 분석은 별도 커밋으로 갱신한다.
- 열린 PR: Copilot 코딩 에이전트 draft 11건(#30~#43), dependabot 8건(#24~#32). 이 문서의 범위 밖이다.

## 7. 검증 기록 (이 세션, 2026-09-15)

| 항목 | 결과 |
|---|---|
| 훅 콜드 실행(`.venv`·`.env`·`node_modules`·테스트 DB 삭제 후) | 통과, 33초 |
| 훅 웜 실행 | 통과, 2초 |
| `ruff check` (샘플 파일) / `mypy` (샘플 모듈) | 통과 |
| `python -m pytest tests/unit/test_per_test_timeout_kill_scope.py` | 2 passed |
| `python -m pytest tests/integration/api/test_health_endpoints.py` (실 Postgres, `aios_test_cloud`) | 7 passed |
| `python scripts/check_zone_manifest.py`, `check_no_bom.py` | 통과 |
| `npm run lint --workspace=apps/web` | 통과 (경고 3건, 오류 0) |
| `npx vitest run apps/web/src/components/AdminRoute.test.tsx` | 7 passed |
