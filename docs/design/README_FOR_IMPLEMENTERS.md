# 미화프로젝트 AIOS — 구현 착수 인계 보고서

> 2026-08-10 세션에서 완료한 전체 문서 감사·보강 작업의 최종 산출물입니다.
> 이 문서 하나만 읽으면 "무엇을 어디에 반영하고, 어떤 순서로 구현을 시작해야
> 하는지" 알 수 있도록 작성했습니다. Claude Code, Cursor AI, 또는 사람
> 개발자 누구든 이 문서를 시작점으로 삼으면 됩니다.

---

## 1. 이 세션에서 한 일 (요약)

기존 기능설계·API·DB·구현규칙 문서들에 대해 **20회 이상의 재점검 라운드**를
거쳐 다음 관점에서 전수 검증했습니다:

1. **내적 일관성** — FD 세부기능 템플릿 완결성, 참조↔정의 매칭, 엔드포인트
   개수 일치, DB FK 무결성
2. **문서 간 정합성** — 15/16/17번 API·백엔드·프론트엔드 문서가 서로
   같은 이름·같은 타입을 쓰는지
3. **원본 정책문서(docx) 대조** — 기능설계가 실제로 정책 원문과 일치하는지,
   정책문서 자체의 결함(8.10 조항 번호충돌)까지 발견해 직접 수정
4. **타임라인 역전** — ADR로 결정한 내용이 그 이전에 쓰인 문서들에 소급
   반영됐는지(총 6곳 이상에서 발견·정정)
5. **번호체계 충돌** — 개발명세서 01~17번 전체가 정책문서의 동일 챕터
   번호를 재사용하고 있던 구조적 문제 → 전 문서 `§`(자기 자신) vs 무접두어
   (정책문서) 표기로 통일
6. **실제 구현 가능성** — "이 문서만 갖고 코드를 짤 수 있는가"를 시뮬레이션
   하며 `.env`, DB 마이그레이션, 서버 실행 명령, SQLAlchemy 세션 계층,
   `.aios-zone`/CODEOWNERS 실체까지 끝까지 추적해 완성

**발견·수정한 것 중 가장 중요한 것들**:
- MFA가 선택이었는데 정책상 "예외 없이 강제"였음 → 필수 게이트로 정정
- Circuit Breaker가 halted/emergency에서도 자동 하향됐음 → 인간 재가동
  승인 없이는 절대 자동 복귀하지 않도록 정정(정책 8.6-B 위반이었음)
- 거래소 API 키 등록 시 출금 권한 여부를 검증하지 않았음 → 최소권한 원칙
  실제 강제
- 비상출금 화이트리스트를 등록하는 기능 자체가 없었음 → FD-11.5 신설,
  위기상황 중 신규등록 원천 차단 포함
- 마켓플레이스 리뷰·분쟁접수·검색정렬 — 프론트엔드 화면은 있는데 백엔드가
  아예 없었음 → FD-13.8~13.10 신설
- `src/db/`(SQLAlchemy 세션·ORM), `src/core/safety/`(FD-9 구현위치),
  `.aios-zone`/CODEOWNERS 실제 내용, pyproject.toml 의존성 목록,
  docker-compose.dev.yml — 전부 "이름만 있고 실체 없던" 상태에서 실제
  작동 가능한 상태로 완성

**최종 검증 상태**(2026-08-10 세션 종료 시점):
- 기능설계문서 FD-1~21: **80개 세부기능 100% 템플릿 완결**
- API 엔드포인트: 15번 문서 = 16번 코드 = **56개로 완전 일치**
- DB 테이블: **23개, FK 무결성 100%**
- 16번 문서 타입 참조: **전수 스캔 결과 미정의 타입 0개**

---

## 2. 파일을 어디에 반영해야 하는가

### 2-A. `outputs/merged/` — 기존 project knowledge 파일을 **그대로 교체**

| 이 파일로 | project knowledge의 이 파일을 교체 |
|---|---|
| `기능설계문서_v1.21.md` | 기능설계문서_v1_0.md (또는 최신본) |
| `00_overview_v1.1.md` | 00_overview-2.md |
| `01_data_models_v1.3.md` | 01_data_models-2.md |
| `02_exchange_adapter_v1.1.md` | 02_exchange_adapter-2.md |
| `03_core_modules_v1.1.md` | 03_core_modules-1.md |
| `04_db_schema_v1.5.md` | 04_db_schema-2.md |
| `05_communication_architecture_v1.1.md` | 05_communication_architecture.md |
| `06_mvp_scope_v1.2.md` | 06_mvp_scope-2.md |
| `07_logging_config_v1.3.md` | 07_logging_config.md |
| `08_test_plan_v1.2.md` | 08_test_plan.md |
| `09_self_redteam_review_v1.2.md` | 09_self_redteam_review.md |
| `10_implementation_task_tree_v1.6.md` | 10_implementation_task_tree-3.md |
| `11_implementation_rules_v1.2.md` | 11_implementation_rules.md |
| `12_functional_design_DEPRECATED.md` | 12_functional_design-2.md — **교체가 아니라 삭제 또는 archive/ 이동 권장** (기능설계문서_v1.21으로 완전 대체됨, 남겨두면 낡은 정보를 최신으로 착각할 위험) |
| `14_marketplace_detailed_v1.1.md` | 14_marketplace_detailed.md |
| `13_multi_tenancy_auth_v1.3.md` | 13_multi_tenancy_auth.md |
| `15_api_spec_rbac_v1.5.md` | 15_api_spec_rbac.md |
| `PROJECT_HISTORY_v1.1.md` | PROJECT_HISTORY.md |
| `AIOS_DevEngine_공유접점문서_v1.1.md` | AIOS_DevEngine_공유접점문서.md (**AIOS·DevEngine 양쪽 프로젝트 모두에 동일하게 업로드** — 이 문서 자체가 요구하는 절차) |

### 2-B. `outputs/` 루트 — **새로 추가**되는 파일

| 파일 | 성격 |
|---|---|
| `16_backend_signatures.md` | 신규 문서 — FD-11~21 FastAPI 백엔드 시그니처 |
| `17_frontend_architecture.md` | 신규 문서 — React/RN 프론트엔드 아키텍처 |
| `ADR-2026-08-10-platform-approval-solo.md` | **Superseded** — 역사적 기록으로만 보존, 실제 참조는 아래 ADR-D 사용 |
| `ADR-2026-08-10-B-tech-stack.md` | 신규 ADR |
| `ADR-2026-08-10-C-order-execution-id.md` | 신규 ADR |
| `ADR-2026-08-10-D-platform-approval-finalized.md` | **신규 ADR — 위 solo ADR을 대체.** 플랫폼 레벨 승인 게이트 정식 확정(5개 질문 가이드라인, 결정유형 7개 목록, 180초 강제대기, 분기별 재검토) |
| `tracking-legal-sla-dr-parallel.md` | **신규 — 병행 트랙 문서.** 법인·인가요건/정량적 SLA/DR전략은 MVP 코딩 착수 게이트가 아님을 명시, 실제 확정 시점까지 정리 |
| `미화프로젝트_AIOS_개발문서_종합본_v3.5.docx` | **정책문서 원본 교체**(v3.4 → v3.5, 4.9에 ADR-2026-08-10-D 각주 추가) |
| `patch-cto-agent-custom-instructions.md` | **project knowledge 파일이 아님** — 이 프로젝트의 커스텀 지침(Claude 프로젝트 설정)을 사람이 직접 수정해야 함, 정확한 diff 포함 |

### 2-C. 사람이 별도로 확인할 것
- `AIOS_DevEngine_공유접점문서_v1.1.md`는 AIOS 프로젝트뿐 아니라 **DevEngine
  프로젝트 지식 저장소에도 동일하게 반영**해야 합니다(문서 자체 §4 절차).
- `patch-cto-agent-custom-instructions.md`의 diff를 프로젝트 커스텀 지침에
  직접 반영해주세요(Claude가 접근 불가능한 영역).

---

## 3. 실제 구현 착수 순서 (Claude Code / Cursor AI용)

### Day 0 — 저장소 셋업
```bash
git init
mkdir -p src/{api/{routers,schemas},services,core/{loader,parser,validator,scanner,event_bus,logging,notifications,indicators,safety,strategy,portfolio,risk,executor},data/models,exchanges,db/models}
# 10번 문서(v1.6) §1.2 폴더트리 전체 참조 — 위는 최상위 골격만
```

1. `10번 문서 §1.1~1.4` 순서대로: `pyproject.toml`(11번 §11.6의 실제
   의존성 목록 사용) → 폴더 골격 → `.gitignore`+`.env.example`(07번 §7.3
   전체 목록 그대로) → `CODEOWNERS`+`.aios-zone`(**10번 문서 §1.4에 실제
   YAML/CODEOWNERS 내용이 있음 — 그대로 파일로 만들면 됨**)
2. `docker compose -f docker-compose.dev.yml up -d`(11번 §11.7 실제 내용)
3. `pip install -e . --break-system-packages`
4. `cp .env.example .env` 후 값 채우기(로컬 개발용 — Bitget Demo, KIS 모의투자)

### Day 1 이후 — 착수 순서 (10번 문서 대분류 순서 그대로)
```
1~8(기반: Loader/Parser/Validator/Scanner/EventBus/Logging/Audit/판단계층 인터페이스)
→ safety/(FD-9, Watchdog·Circuit Breaker·DataDistrust·Reconciliation)
→ 9·10(SURGE·인간승인)
→ 11~13(계정·거래소·마켓플레이스)
→ 18(운영자 도구)
→ 14~16(편집기·적합성평가·실행제어판)
→ 19~20(포트폴리오·보고서)
→ 21(모바일, 11~20 API 전체 안정화 이후)
```

### 절대 하지 말아야 할 것 (FROZEN Zone)
`.aios-zone`이 `FROZEN`으로 지정한 경로(`src/core/strategy/`,
`src/core/portfolio/`, `src/core/risk/decision/`, `src/core/executor/`,
`aios/kernel/policy/`, `aios/kernel/permission/`)는 **15.6-D 종료조건
충족 전까지 어떤 PR도 대상 불가**입니다. `src/core/safety/`는 "감시"이지
"판단"이 아니라서 FROZEN이 아니라 SCAFFOLD입니다 — 이 구분을 절대
혼동하지 마세요(정책문서 15.6-A 원칙).

---

## 4. 아직 사람이 결정해야 하는 것 (구현 착수와 무관하게 병행)

| 항목 | 상태 |
|---|---|
| Human Approval 플랫폼 레벨 승인권자 2인 | **정식 확정**(ADR-2026-08-10-D) — 5개질문/7개결정유형/180초/분기재검토까지 완결, 실제 2인 확보는 여전히 미착수 |
| 법인·자본시장법 인가요건 법률검토 | 미착수 — **병행 트랙**(`tracking-legal-sla-dr-parallel.md` 참조, Phase 1 코딩 착수를 막지 않음) |
| 정량적 SLA·DR 전략 | 의도적 미정 — **병행 트랙**(위 문서 참조, Phase 2~3/프로덕션 직전 확정) |
| 개인정보보호법·신용정보법 데이터규제 대응 | 미착수 |
| `AIOS_DevEngine_공유접점문서` DevEngine 측 반영 | 사람이 직접 확인 필요 |
| 프로젝트 커스텀 지침(21.4 블로커 목록) 갱신 | `patch-cto-agent-custom-instructions.md` 참조 |
| FD-13.11(Certified Badge 지속 재검증) | 의도적 보류 — FD-9.6 인프라 이후 착수 |
| **FD-11~21 정식 레드팀 라운드** | **미착수 — 다음 우선 액션으로 권고.** 정책문서(종합본) 자체는 11라운드 레드팀을 거쳤고 00~08번 기술스펙도 09번에서 1회 검토됐지만, FD-11~21(멀티테넌시·마켓플레이스·프론트엔드)은 이번 세션의 "재점검 라운드"(사후 다각도 스캔)가 사실상 대신했을 뿐 09번 §9.1과 같은 **사전 계획된 체계적 레드팀**은 아직 한 번도 없었다(09번 §9.3 참조). DevEngine 프로젝트와의 비교 검토에서도 동일한 결론 — "다음으로 필요한 건 문서 확장이 아니라 실제 적대적 레드팀 라운드 실행"임을 재확인. |

---

## 5. 문서 읽는 순서 (사람이 온보딩할 때)

> 이전 버전은 6개 문서만 나열하고 나머지 11개(01/02/03/05/06/07/08/09/11/13/14번)가
> 어디서 읽혀야 하는지, 그리고 각 단계에서 다음 단계로 왜 넘어가는지가
> 비어 있었습니다. 아래는 "이 문서를 다 읽고 나면 다음에 무엇을 알아야
> 하는지"를 사이사이에 채운 버전입니다.

### 1단계 — 왜 이 프로젝트가 지금 이 모습인가 (배경)
**`PROJECT_HISTORY_v1.1.md`** 를 처음부터 읽습니다. 이 문서는 "지금 문서
20여 개가 왜 이렇게 나뉘어 있는지"에 대한 설명이라, 이걸 안 읽고 바로
스펙으로 들어가면 나중에 "왜 04번과 13번이 DB 스키마를 나눠서 다루지?"
같은 질문에 계속 부딪힙니다.
→ 다 읽으면 자연스럽게 §0(문서 생태계 지도)에서 다음 단계로 안내됩니다.

### 2단계 — 전체 지도 확인
**`00_overview_v1.1.md`** §0.5(문서 구성 표)로 17개 파일이 각각 뭘 다루는지
한눈에 확인합니다. §0.2(기술스택 확정표)도 여기서 같이 봅니다 — 이후
모든 코드 예시가 이 스택(FastAPI/PostgreSQL/SQLAlchemy async/React)을
전제로 쓰여 있기 때문에, 이걸 먼저 알아야 04번 이후 문서의 코드가
자연스럽게 읽힙니다.
→ §0.3(디렉터리 매핑)이 "6장 Repository 구조"를 언급하므로, 다음은 정책문서.

### 3단계 — 정책·아키텍처 원문 (선택적 발췌)
**`미화프로젝트_AIOS_개발문서_종합본_v3.5.docx`** 전체를 다 읽을 필요는
없습니다 — 아래 4단계 이후의 모든 문서가 필요한 조항을 이미 인용하고
있습니다. 다만 프로젝트의 **안전 철학 자체**를 이해하려면 최소 이 4개
챕터는 먼저 읽는 걸 권장합니다: **8장**(Trading Core, Master Authority·
Kill Switch), **9장**(Strategy Lifecycle, Zone 재분류), **15장**(DevEngine
연계 경계, FROZEN/SCAFFOLD 분류), **20장**(Go/No-Go 체크리스트). 이 4개를
모르면 이후 "왜 FD-9만 유독 안전장치가 겹겹이 쌓여 있는지" 이해가 안 됩니다.

### 4단계 — 데이터 계층 3형제 (01·02·03번)
정책을 실제 타입으로 처음 옮기는 문서 3개를 **이 순서로**:
- **`01_data_models_v1.3.md`** — Pydantic 모델(Order, Position, FSMStrategyConfig 등).
  여기서 정의된 타입이 이후 모든 문서(04번 DDL, 16번 API 시그니처)의 어휘가 됩니다.
- **`02_exchange_adapter_v1.1.md`** — 거래소 인터페이스. 01번의 `Order`/`Position`을
  입출력으로 쓰는 실제 예시라서 01번 없이는 이해가 어렵습니다.
- **`03_core_modules_v1.1.md`** — `src/core/` 8개 모듈(Loader~Executor). 01/02번의
  타입들을 실제로 소비·가공하는 함수 시그니처.
→ 이 3개를 보고 나면 "FROZEN이 정확히 어느 코드 파일들을 가리키는지"가
처음으로 구체적인 경로(`src/core/strategy/` 등)로 눈에 들어옵니다.

### 5단계 — 모듈들이 서로 어떻게 말을 거는가 (05번)
**`05_communication_architecture_v1.2.md`** — Event Bus. 4단계의 모듈들이
서로 격리돼 있으면서도(각자 다른 파일) 어떻게 통신하는지(`{domain}.
{entity}.{event_type}` 토픽)를 다룹니다. 이걸 먼저 알아야 이후 FD 문서들의
"~ 이벤트 발행" 같은 표현이 막연하게 느껴지지 않습니다.

### 6단계 — 지금 뭘 만들고 뭘 안 만드는가 (06번)
**`06_mvp_scope_v1.2.md`** — Phase 1의 정확한 경계선. §6.4(명시적 제외)를
특히 꼼꼼히 읽으세요 — "이건 나중에"라고 착각하기 쉬운 항목들이 실제로는
Phase 1에 포함되는 경우(마켓플레이스 골격 등)와, 반대로 Phase 1 대상
기능이 은근슬쩍 제외 항목에 의존하려는 경우(FD-9.2의 SOR 폴백 사례 참조)를
여기서 미리 걸러낼 수 있습니다.

### 7단계 — 로그·설정·환경변수 (07번)
**`07_logging_config_v1.3.md`** — `risk_policy.yaml`, `suitability_scoring.yaml`,
`.env.example`. 이 문서를 실제로 복사해서 로컬 환경을 준비하는 게 코딩
착수 직전 마지막 실무 단계입니다(§11.7 docker-compose와 세트).

### 8단계 — 무엇을 만들고 있는지 (기능설계문서, 12번 대신)
**`기능설계문서_v1.21.md`** — "무엇을 만드는가"(FD-1~21). 4~7단계에서
"어떤 부품들이 있는지"를 알았으니, 이제 "그 부품들로 어떤 기능을 완성하는지"를
읽습니다. **舊 `12_functional_design_DEPRECATED.md`는 이 문서로 완전히
대체됐으니 건너뛰어도 됩니다**(archive 권장).

### 9단계 — 여러 사용자를 어떻게 격리하는가 (13번)
**`13_multi_tenancy_auth_v1.3.md`** — users/exchange_credentials 등 멀티테넌시
근간. 기능설계문서 FD-11~13이 이 문서의 테이블을 계속 참조하므로, FD-11
언저리를 읽다가 막히면 이 문서로 돌아옵니다.

### 10단계 — 마켓플레이스만 따로 깊게 (14번)
**`14_marketplace_detailed_v1.1.md`** — FD-13이 "골격"만 다루고 실제
가격정책·검증전환·검색정렬·분쟁처리 상세는 이 문서로 위임했습니다. FD-13을
읽다가 "가격은 정확히 어떻게 계산하지?" 같은 질문이 들면 여기로 옵니다.

### 11단계 — 어떻게 만드는지 (04 → 15 → 16 → 17번)
이제 "무엇을"(8~10단계)을 알았으니 "어떻게"로 넘어갑니다. 반드시 이 순서로
읽으세요 — 각자 앞 문서를 전제로 합니다:
- **`04_db_schema_v1.6.md`** — 실제 테이블(01번 Pydantic을 DDL로).
- **`15_api_spec_rbac_v1.5.md`** — 04번 테이블을 HTTP로 노출하는 계약.
- **`16_backend_signatures.md`** — 15번 계약을 FastAPI로 구현하는 실제
  시그니처(라우터→서비스→DB 3계층, `src/db/` 세션 계층 포함).
- **`17_frontend_architecture.md`** — 16번 API를 소비하는 React 화면.

### 12단계 — 테스트·자체검증 (08·09번)
**`08_test_plan_v1.2.md`** → **`09_self_redteam_review_v1.2.md`** 순서로.
09번은 "이 프로젝트가 스스로를 어떻게 의심해왔는지"의 기록이라, 지금까지
읽은 문서들에 남아있을 수 있는 결함의 **패턴**(참조-정의 불일치, 타임라인
역전 등)을 미리 알아두면 실제 코딩 중 비슷한 걸 더 빨리 알아챌 수 있습니다.

### 13단계 — 코드 품질·환경 공통규칙 (11번)
**`11_implementation_rules_v1.2.md`** — Money 타입, 시간동기화, pyproject.toml
실제 의존성, docker-compose. **코딩을 시작하기 직전 마지막으로 펼쳐두는 문서**
입니다.

### 14단계 — 무슨 순서로 만드는지 (10번)
**`10_implementation_task_tree_v1.7.md`** — 지금까지 8~13단계에서 읽은 모든
문서의 내용을 실제 폴더·파일·커밋 단위로 잘게 쪼갠 작업 목록입니다. §1.1부터
그대로 따라가면 됩니다. **여기가 실제 구현의 출발점**입니다.

> ⚠️ **위 1~14단계는 "온보딩 시 읽는 순서"이지 "실제 구현 착수 순서"가
> 아닙니다** — 예를 들어 8~10단계(기능설계문서→13번→14번)는 이해를 돕기
> 위한 읽기 순서일 뿐, 실제 코딩은 10번 문서가 명시한 순서(대분류
> 1→...→21, 예외: FD-17이 9·10보다 먼저, FD-11.6 리프만 FD-16 이후로
> 미룸 — "흐름도 매끄러움 재점검" 라운드에서 확인)를 따릅니다. 착수
> 순서가 궁금하면 항상 10번 문서를 최종 기준으로 삼으세요.

### 부록 — 그 외 참조용 문서
- ADR 4건(솔로승인/기술스택/execution_id/**플랫폼승인 정식확정**) — 특정
  결정의 "왜"가 궁금할 때
- `AIOS_DevEngine_공유접점문서_v1.2.md` — DevEngine 프로젝트와 접점이 생길 때만
- `tracking-legal-sla-dr-parallel.md` — 코딩과 무관하게 진행되는 병행 트랙 확인용
- `patch-cto-agent-custom-instructions.md` — 사람이 프로젝트 설정을 갱신할 때

---

*이 보고서 자체와 위 산출물 전체는 2026-08-10 단일 세션에서 생성됐습니다.
질문이나 추가 검증이 필요하면 이 세션의 대화 이력을 참조하세요.*
