# 미화프로젝트 — 개발 명세서 (Technical Specification) v1.1

> **v1.1(2026-08-10) = "0번부터 재검토" 라운드.** §0.1~0.6 헤더를 "§0.X"로
> 통일(정책문서 0장 하위조항 자체는 없어 실제 충돌 위험은 낮았으나 일관성
> 위해 적용). §0.2 기술스택 표를 "Draft/확인필요"에서 ADR-2026-08-10-B
> 기준 확정으로 갱신(FastAPI 등 누락 항목 추가). §0.5 문서 구성 표에
> 11~17번 및 기능설계문서 추가(그동안 00~10번까지만 나열돼 있었음). 상위
> 문서 버전 참조를 v3.0→v3.4로 갱신.

> 상위 문서: `미화프로젝트_AIOS_개발문서_종합본_v3.4.docx` (정책·아키텍처 — v3.4: 8.10 Audit Log 조항 번호충돌 정정)
> 본 문서의 역할: 위 아키텍처 문서를 실제 코드로 옮기기 위한 함수 시그니처·클래스·DB 스키마 수준의 명세.
> 정책적 판단(왜 이렇게 설계했는가)은 상위 문서를 참조하고, 본 문서에서는 반복하지 않는다.

---

## §0.1 범위 (Scope)

본 문서는 **15.6-A SCAFFOLD Zone**에 해당하는 항목만 다룬다.

| Zone | 본 문서 포함 여부 | 비고 |
|---|---|---|
| SCAFFOLD | ✅ 전체 포함 | 인터페이스·데이터모델·타입 정의. 실행 로직 없음 |
| FROZEN (Risk/Executor/Portfolio/Strategy 실제 판단로직) | ⚠️ 인터페이스(시그니처)만 포함, 구현체는 `NotImplementedError` | 15.6-D 종료조건 충족 전까지 실제 로직 작성 금지 |
| OPEN (docs/tests/configs) | 본 문서 대상 아님 | 별도 관리 |

FROZEN Zone 인터페이스는 **"이 함수가 존재하고, 이런 입출력을 가질 것이다"**를 코드로 명확히 해두는 것이 목적이며, 함수 내부에 실제 리스크 계산·주문 로직을 채우는 것은 15.6-D 이후 별도 승인을 받아야 한다.

## §0.2 기술 스택 (확정 — ADR-2026-08-10-B, v1.1 병합)

> **v1.1(2026-08-10)**: 이 표는 오랫동안 "Draft, 확인 필요"였으나 ADR-2026-08-10-B
> (2026-08-10 세션)로 이미 확정된 지 오래인데도 본문이 갱신되지 않았던 것을
> "0번부터 재검토" 라운드에서 발견해 완결. 웹 프레임워크(FastAPI) 행도
> 추가 — 15번 API 스펙이 이미 오래전부터 존재했는데 이걸 구현할 프레임워크
> 자체가 이 표에 없었음.

| 항목 | 확정값 | 근거 |
|---|---|---|
| 언어 | Python 3.11+ | DevEngine 스펙(pytest/pip 언급)과의 일관성 |
| 웹 프레임워크 | **FastAPI** | Pydantic v2 네이티브 통합, 비동기 지원, OpenAPI 자동생성(15번 API 스펙 수동관리 부담 경감) — ADR-2026-08-10-B |
| 데이터 모델 | Pydantic v2 | Task/Strategy 등 구조화된 스키마 검증에 적합, DevEngine과 공유 가능 |
| DB | **PostgreSQL** | DDL(04번)이 이미 Postgres 문법(JSONB, gen_random_uuid() 등) 전제로 작성됨 — ADR-2026-08-10-B |
| ORM | **SQLAlchemy 2.0 (async)** | Pydantic ↔ DB 매핑, FastAPI 비동기와 자연스럽게 맞물림 — ADR-2026-08-10-B |
| DB 드라이버 | **asyncpg** | SQLAlchemy async + PostgreSQL 표준 조합 |
| 인증 토큰 | **JWT (PyJWT)** + FastAPI `OAuth2PasswordBearer` | FD-11.1 |
| 비동기 처리 | asyncio + httpx/aiohttp | Exchange Adapter의 WebSocket/REST 병행 처리(7.4) |
| 프론트엔드(웹) | **React + TypeScript** | 모바일(React Native, FD-21)과 비즈니스 로직 공유 — ADR-2026-08-10-B |
| 프론트엔드 상태관리 | TanStack Query + Zustand | 서버상태/클라이언트상태 분리 |

## §0.3 디렉터리 매핑 (6장 Repository 구조와 연결)

> **세분화된 실제 폴더 트리 및 파일 단위 작업 매핑은 `10_implementation_task_tree.md` §10.3을 참조한다.** 아래는 최상위 구조 요약이다.

```
src/
├── core/       ← loader/parser/validator/scanner(SCAFFOLD) + strategy/portfolio/risk/executor(FROZEN 인터페이스만)
├── data/       ← models/ (01번 문서)
├── exchanges/  ← common/(02번 인터페이스), bitget/, kis/, bithumb/(보류)
└── db/         ← migrations/, models/ (04번 문서)

config/         ← risk_policy.yaml(07번), .env
docs/development/ ← 본 문서 시리즈 저장 위치
tests/          ← unit/, integration/, e2e/ (08번 문서)
```

## §0.4 명명 규칙 (Naming Convention)

- 클래스: `PascalCase` (예: `AIOSTask`, `ExchangeAdapter`)
- 함수/변수: `snake_case` (예: `place_order`, `get_ticker`)
- 상수: `UPPER_SNAKE_CASE`
- Enum: `PascalCase` 클래스명 + `UPPER_SNAKE_CASE` 멤버 (예: `OrderStatus.PARTIALLY_FILLED`)
- 추상 클래스/인터페이스: 접미사 없이 명사형 (예: `ExchangeAdapter`, 구현체는 `BitgetAdapter`)
- Draft 상수(8.2-B Risk 수치 등): 반드시 `config/risk_policy.yaml` 등 외부 설정 파일로 분리, 코드에 하드코딩 금지 (13.1 개발원칙, 20.1-B §D 참조)

## §0.5 문서 구성 (v1.1 병합 — "0번부터 재검토" 라운드에서 발견: patch-00-overview-doc-index.md로만 존재하고 본문 미반영이었던 것 완결)

| 파일 | 내용 |
|---|---|
| `00_overview.md` | 본 문서 |
| `01_data_models.md` | AIOSTask, FSMStrategyConfig, MarketData, Trading 데이터 모델 (Pydantic) |
| `02_exchange_adapter.md` | Exchange Adapter 공통 인터페이스 (ABC) — Bitget/KIS |
| `03_core_modules.md` | src/core/ 8개 모듈 함수 시그니처 |
| `04_db_schema.md` | 실제 DB 테이블 DDL |
| `05_communication_architecture.md` | 모듈 간 통신방식 (In-process Event Bus), 동시성 모델, 에러처리 원칙 |
| `06_mvp_scope.md` | Phase 1 MVP 정확한 스콥, Definition of Done, 명시적 제외 목록 |
| `07_logging_config.md` | 로깅 포맷, risk_policy.yaml 구조, Secrets 관리 |
| `08_test_plan.md` | 테스트 피라미드, 단위/통합/E2E 케이스, CI 개요, 백프레셔 정책 |
| `09_self_redteam_review.md` | 00~08 기술 스펙 자체 레드팀 재검토 및 반영 내역 |
| `10_implementation_task_tree.md` | 최소단위 구현 작업 트리, 폴더 구조, Git 커밋/PR 컨벤션 |
| `11_implementation_rules.md` | 통화(Money 타입)·정밀도·커스텀 예외 계층·시간동기화·데이터보존 등 모듈 횡단 공통 규칙(09번 후속 2차 검토) |
| `기능설계문서.md` | FD-1~21 기능설계(현재 v1.17) — 12번(초안)에서 통합·확장 |
| `13_multi_tenancy_auth.md` | 멀티테넌시·인증 신규 근간 — users/exchange_credentials/strategy_listings 등 |
| `14_marketplace_detailed.md` | 마켓플레이스 완성형 상세(가격정책·평판·검증전환·검색정렬·분쟁처리) |
| `15_api_spec_rbac.md` | REST API 명세·에러코드·RBAC(현재 v1.5) |
| `16_backend_signatures.md` | FD-11~21 FastAPI 백엔드 시그니처(2026-08-10 세션 신설, 현재 v1.4) |
| `17_frontend_architecture.md` | React/React Native 프론트엔드 아키텍처(2026-08-10 세션 신설, 현재 v1.5) |
| `AIOS_DevEngine_공유접점문서.md` | AIOS/DevEngine 프로젝트 간 동결 인터페이스 |

## §0.6 상태 표기

본 문서의 모든 코드는 다음 상태 중 하나로 태깅된다 (상위 문서 0장 상태 범례와 동일):

- `# STATUS: SCAFFOLD-READY` — 지금 바로 구현 가능
- `# STATUS: FROZEN-INTERFACE-ONLY` — 시그니처만 확정, 본문 구현 금지 (15.6-D 전)
- `# STATUS: DRAFT` — 확정 전, 리뷰 필요
