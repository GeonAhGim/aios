# AIOS — AI Investment Operating System (미화프로젝트)

기관·자산운용사급을 목표로 하는 AI 트레이딩 OS. 결정론적 리스크 엔진이 항상 최상위 권위를 갖고(8.2-A
Master Authority), 실시간 주문 경로에는 LLM이 개입하지 않는다. 현재 단계는 **PAPER 전용**이며 LIVE 경로는
코드 레벨 하드가드로 봉쇄돼 있다(ADR-2026-08-29-E).

## 저장소 구조

| 경로 | 내용 |
|---|---|
| `src/` | 백엔드 (FastAPI · asyncpg · Alembic). `core/`(판단·안전 계층), `services/`(실행 루프·주문·마켓플레이스), `foundation/`(헥사고날 컨텍스트: trust/mandates/evidence/connections/validation/risk_gate/paper_control/reconciliation/performance), `exchanges/`(Bitget·KIS·NH 어댑터), `api/`(라우터·스키마), `db/migrations/` |
| `tests/` | 실 Postgres 통합·단위·적대적 테스트 (`docs/TESTING.md`) |
| `frontend/` | React 19 + Vite 모노레포 (`apps/web`, `packages/*`) — 이전 `mihwa-aios-frontend` 이력 보존 |
| `docs/design/` | 설계문서 원본: 00~17 개발명세, 기능설계문서, ADR, `codex/`(L3 명세 71~81·표준 103~108) |
| `docs/specs/` | L4 구현 명세 5종(기관급 요구 → 최소단위 모듈·리프) + DevEngine 백로그 |
| `docs/FULL_AUDIT_2026-09-02.md` | 전수 점검 보고서 — 판정·P0·배정표(§2-B)·권고 순서 |
| `docs/RED_TEAM_FINDINGS.md` | 레드팀 장부 (#01~#41) |
| `scripts/` | `check_zone_manifest.py`(zone 게이트), `setup_test_db.py`(세션별 테스트 DB) |
| `config/risk_policy.yaml` | 8.2-B 리스크 지표·실행 루프 주기(판단 계층 설정의 단일 출처) |
| `.aios-zone` | FROZEN / FROZEN_PAPER_ONLY / SCAFFOLD / OPEN 매니페스트 |

정책·Guard(Meta-Control Plane)는 별도 저장소 [`GeonAhGim/aios-meta`](https://github.com/GeonAhGim/aios-meta)에 있으며 사람만 수정한다.
CI `guards` job이 고정 커밋으로 checkout해 모든 변경을 검사한다.

## 시작

```bash
docker compose -f docker-compose.dev.yml up -d
python -m venv .venv && .venv/Scripts/pip install -e ".[test,dev]"
cp .env.example .env            # 값 채우기
.venv/Scripts/alembic upgrade head
.venv/Scripts/uvicorn src.main:app --reload
```

테스트·게이트는 `docs/TESTING.md`, 개발 조직(Orchestrator·헤드리스 worker·PM)은 `docs/specs/README.md`와
`docs/FULL_AUDIT_2026-09-02.md` §2-B를 본다.
