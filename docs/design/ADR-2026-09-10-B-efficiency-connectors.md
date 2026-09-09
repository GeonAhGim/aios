# ADR-2026-09-10-B: 워커 효율 커넥터 — 저장소 CLAUDE.md·결정론 훅을 먼저, MCP는 측정하며 붙인다

## Status
Accepted (2026-09-10, CTO). 사용자 지시 "효율을 높일 MCP나 커넥터가 있으면 알아서 극대화. 다른 방법도 좋다".

## Context
- 워커는 `claude -p`(headless)로 뜨며 저장소에 CLAUDE.md가 없다. 규칙(영문 주석·전체 pytest 금지·create_task만·라이브 pm 파일 편집 금지 등)이
  전부 WORKER_PROMPT_*.md에만 있어 프롬프트가 길고, 워커는 매번 저장소 구조를 다시 탐색한다.
- 실측(OPS-29): 긴 실행의 71%가 도구 대기, 컨텍스트 재읽기 17M 토큰. MCP는 도구 스키마가 컨텍스트를 차지하므로 무조건 많이 붙이면 오히려 손해다.
- Copilot 한도 도달로 외부 리뷰 엔진 의존은 줄여야 한다.

## Decision — 효과/비용 순서로 도입, 각 단계는 OPS-29 계측으로 검증
| 순서 | 수단 | 효과 | 비용 |
|---|---|---|---|
| 1 | **저장소 CLAUDE.md**(aios·pm): 구조 지도, 명령(테스트·게이트·마이그레이션), 규칙 전부, 금지 목록, 자주 틀리는 것 | 모든 워커가 자동 로드 → 탐색 턴 감소, 프롬프트 절반으로 | 없음 |
| 2 | **결정론 훅**(`.claude/settings.json`): Edit 후 ruff --fix·format 자동, Bash 전 가드(전체 pytest·git stash·C:/aios/pm 직접 편집·백그라운드 실행 차단), 커밋 전 게이트 요약 | 실패 사이클 자체를 제거 | 없음 |
| 3 | **검토 컨텍스트 MCP + 스킬**(ADR-2026-09-10-A) | QA·리뷰 턴 절반 | 진행 중 |
| 4 | **LSP MCP**(Serena 또는 mcp-language-server: pyright·tsserver): 정의·참조·심볼 검색 | grep/read 턴 감소(대형 저장소에서 가장 큼) | 스키마 ~2K 토큰, 인덱스 |
| 5 | **Playwright MCP**(frontend·QA 레인만): 화면 실동작 검증 | UX 리프 D2 증빙 자동화 | node 프로세스 |
| 6 | **문서 MCP**(Context7): FastAPI·SQLAlchemy·klinecharts·pandas-ta-classic·Bitget 문서 최신본 | 잘못된 API 추측 감소 | 네트워크 |
| 7 | Postgres 읽기 전용 MCP(backend·QA): 스키마·마이그레이션 head·리플레이 표 조회 | 임시 스크립트 감소 | 낮음 |
- 레인당 MCP는 최대 3개. 도구 허용 목록(`--allowedTools`)으로 스키마 노출을 제한한다.
- 각 단계 도입 24h 후 턴·시간·cache_read·실패율을 비교해 개선이 없으면 되돌린다(OPS-29 metrics).
- 프롬프트 슬림화: WORKER_PROMPT_*.md는 task 고유 지시만 남기고 공통 규칙은 CLAUDE.md로 옮긴다.

## Rejected
- 모든 레인에 MCP 6종 일괄 부착: 컨텍스트 낭비. 
- 외부 SaaS 커넥터(Sentry·Datadog 등): 아직 배포가 없어 효과 0.

## Consequences
- 구현: CLAUDE-1(aios CLAUDE.md), CLAUDE-2(pm CLAUDE.md + 프롬프트 슬림), OPS-39(훅·settings), OPS-40(LSP·Playwright·Context7·Postgres MCP 레인별 배선 + 계측·롤백).
