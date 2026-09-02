# Enterprise Architecture Full Audit and Remediation Brief v1.0

> 상태: **Canonical remediation brief**  
> 대상: AIOS 설계 문서 33~102, `mihwa-aios` 백엔드 저장소, 확인 가능한 작업공간 경계  
> 작성일: 2026-09-01  
> 목적: Claude Code와 Codex가 기능 확장 전에 공통으로 따라야 할 보완 우선순위와 완료 증거를 고정한다.

---

## 1. 경영 요약

AIOS는 분석·전략·포트폴리오·리스크·실행·거래소·마켓플레이스·신뢰·운영과 프런트엔드 경험을 포괄하는 **목표 아키텍처**를 확보했다. 설계 문서 33~102는 모두 존재하며, 사용자가 요구한 Super-Quant급 구조의 폭은 충족한다.

그러나 현재 판정은 다음과 같다.

| 판정 영역 | 상태 | 의미 |
|---|---|---|
| 제품·도메인 범위 | 양호 | 주요 기능·서비스·엔진의 상위 구조가 존재한다. |
| 모듈 경계 | 부분 양호 | 분석, 전략, 실행, Provider Adapter, Marketplace를 분리한 방향은 적절하다. |
| 상세 구현 계약 | 미완료 | 모든 모듈에 대해 API/event/schema/권한/실패 처리/인수 기준이 동결된 것은 아니다. |
| 안전·보안·운영 준비 | 미완료, P0 존재 | PAPER/LIVE 격리, 자격증명, 멱등성, 감사·정산·복구의 실증이 부족하다. |
| 설계-코드-테스트 추적성 | 미완료 | 최신 설계군과 실제 저장소·CI·프런트엔드·IaC의 일대일 매핑이 없다. |
| 사용자 대상 출시 | 불가 | P0 해소와 staging 증거 없이 PAPER 서비스 또는 LIVE/Marketplace로 이동할 수 없다. |

**운영 원칙:** 신규 기능의 양을 늘리는 것보다 P0 보완과 추적성 확립이 우선이다. 수익을 보장하거나 손실 없는 자동운용을 암시하는 제품·UX·마케팅 표현은 금지한다.

---

## 2. 검사 범위와 사실

### 2.1 검사 완료 범위

1. 설계 문서 33~102 총 70개 파일의 존재·연속성·주제 범위를 확인했다.
2. Foundation 상세 명세(73~81), 레드팀 문서(82~88), 분석·전략·백엔드·프런트엔드 확장 설계(89~102)를 교차 점검했다.
3. `mihwa-aios`의 Git 상태, 테스트 구조, 정적 분석을 확인했다.
4. 작업공간에서 `mihwa-aios-frontend` 저장소는 확인되지 않았다. 따라서 프런트엔드 구현과 설계의 실제 일치 여부는 검증하지 못했다.

### 2.2 검증 결과

| 항목 | 결과 | 비고 |
|---|---|---|
| `AIOSproject` Git 상태 | clean | branch `main`, audit 시점 HEAD `f830614` |
| `mihwa-aios` Git 상태 | clean | branch `codex/update-paper-adapter-finding-status`, audit 시점 HEAD `8c4a840` |
| Python 정적 검사 | 통과 | `py -m ruff check .` |
| AIOS 테스트 | 미실행 | 전용 `TEST_DATABASE_URL`이 없으면 안전하게 중단되도록 구성됨 |
| 전역 pytest 결과 | 무효 | 작업공간의 별도 schema-validation 패키지까지 수집되었으므로 AIOS 테스트 결과로 사용하지 않는다. |

### 2.3 중요한 해석

`TEST_DATABASE_URL` 부재로 테스트가 중단되는 것은 운영/개발 DB 오접속을 방지한다는 점에서 옳다. 다만 개발자가 바로 재현할 수 있는 격리 테스트 DB, compose/CI service, seed 및 정리 절차가 제공되지 않는 것은 출시 전 해결해야 할 운영성 결함이다.

---

## 3. P0 — 기능 확장보다 먼저 닫을 항목

### P0-01. 레드팀 발견사항을 실행 가능한 단일 위험 등록부로 전환

**문제:** 82~88번 문서는 P0/P1 발견사항과 캠페인 종료 조건을 잘 정리했지만, 실제 담당자·저장소·PR·검증 증거·마감 게이트를 가진 단일 등록부는 없다.

**조치:** `risk_id`, severity, exploit precondition, affected component, owner, target repo, remediation PR, verification test, evidence URI, residual risk, approval, due gate를 필수 필드로 한 위험 등록부를 만든다.

**완료 조건:** 모든 P0가 `verified-closed` 또는 임원/보안 책임자가 승인한 명시적 `accepted-risk` 상태이며, 증거 링크가 존재한다.

### P0-02. PAPER/LIVE·자격증명·실행 경계의 물리적 격리

**문제:** 레드팀은 LIVE 제어면 존재 가능성, 프로세스 내 복호화된 자격증명, 권한·egress·revocation 경계 부족을 지적했다. 단순 feature flag로는 사용자의 실제 자산을 보호할 수 없다.

**조치:**

- PAPER와 LIVE를 서로 다른 runtime, network egress policy, secret scope, database/schema, queue/topic, deployment approval으로 분리한다.
- Provider credential은 최소권한·암호화·rotation·revocation·사용 목적·evidence를 가진 vault capability로만 접근한다.
- 분석/전략/LLM/MCP는 주문 제출·자격증명 접근 권한을 직접 갖지 않는다.
- Kill switch와 provider revoke는 process-local cache가 아닌 권위 있는 정책 저장소와 egress 차단으로 즉시 강제한다.

**완료 조건:** 침해·오동작 drill에서 PAPER가 LIVE credential·network·order intent에 접근할 수 없고, kill switch 이후 새 주문이 제출되지 않음을 독립 테스트와 로그로 증명한다.

### P0-03. 주문 멱등성·동시성·정산의 원자성

**문제:** 승인과 주문 제출 사이의 TOCTOU, 전역이 아닌 idempotency key, 잔고 중복 배분, provider 결과 불명 상태가 손실과 중복 주문으로 이어질 수 있다.

**조치:** OrderIntent의 tenant/account/provider/strategy/version/time-window를 포함한 idempotency scope, outbox/inbox, reservation ledger, command state machine, provider reconciliation authority를 설계·구현한다.

**완료 조건:** network retry, duplicate delivery, partial fill, provider timeout, failover, stale approval, concurrent allocation의 integration test가 모두 통과하며 합계 보존·감사 추적이 증명된다.

### P0-04. 감사증적·정산·복구의 권위 원천 확정

**문제:** hash chain만으로 외부 변조·키 탈취·provider 최종성·corporate action을 해결할 수 없다.

**조치:** immutable/WORM evidence store, key/HSM boundary, time source, provider statement ingestion, reconciliation finality rule, correction event, backup/restore RPO/RTO를 명시한다.

**완료 조건:** 독립 복구 rehearsal에서 지정 RPO/RTO를 만족하고, 한 주문의 정책·입력·승인·제출·체결·정산·수정 이력을 재현할 수 있다.

### P0-05. 최신 설계군의 단일 진입점과 추적성

**문제:** 33~102 문서는 존재하지만 `00_overview_v1.1.md`, `README_FOR_IMPLEMENTERS.md` 등 기존 마스터 문서에서 이 설계군으로 가는 링크 수가 0이다. Claude Code가 구 문서만 읽고 구현할 위험이 있다.

**조치:** 문서 포털/manifest를 만들고 모든 문서에 `status`, `owner role`, `supersedes`, `depends on`, `implemented by`, `verification evidence` 메타데이터를 둔다. 요구사항→ADR→계약→코드 모듈→테스트→운영 runbook의 traceability matrix를 생성한다.

**완료 조건:** 신규 작업이 작업 패키지 하나와 연결되고, 그 패키지에서 최신 canonical 문서·관련 ADR·계약·테스트를 한 화면에서 찾을 수 있다.

---

## 4. P1 — Foundation 완료 후 병렬로 강화할 항목

### P1-01. 모든 도메인의 L3/L4 구현 계약화

73~81은 Foundation에 대해 비교적 상세하다. 반면 분석(89), 엔진 경계(90), Visual Strategy Studio(91), 전체 백엔드(92), 프런트엔드(93~102)는 좋은 구조 문서이지만 다수 모듈이 아직 구현 계약 수준까지 내려오지 않았다.

각 모듈은 최소한 다음을 가진다.

- API/OpenAPI 또는 비동기 event schema와 versioning 규칙
- 입력·출력·오류·재시도·멱등성·권한·tenant context
- DB data ownership, retention, PII/data classification
- 성능/SLO·관측성·alert·runbook
- failure mode, rollback, feature gate
- unit/integration/contract/E2E acceptance criteria

### P1-02. Frontend–BFF–Backend 계약과 실제 저장소 연결

93~102의 UX 방향은 적절하나, 현재 작업공간에 프런트엔드 구현 저장소가 없어 검증하지 못했다.

**조치:** canonical frontend repository·branch·package ownership을 지정하고, page route → BFF read model → backend contract → loading/error/permission state → visual regression/E2E를 매핑한다. 고밀도 차트는 data freshness, downsampling, rendering latency, accessibility, mobile fallback의 성능 예산을 명시한다.

### P1-03. Provider·시장 데이터·법규 관할성

거래소/증권사/지갑마다 capability, rate limit, order semantics, read/write scope, jurisdiction, data license, market session이 다르다.

**조치:** Provider Capability Profile과 Country/Entity Policy를 계약으로 만들고, 지원 국가·상품·계좌 유형·자동화 수준을 feature gate로 강제한다. 법률·준법·개인정보 책임자의 승인 증적 없이는 customer-facing claim, LIVE, Marketplace 판매를 열지 않는다.

### P1-04. AI/LLM 및 연구 공급망 거버넌스

AI 결과는 분석 신호·가설·설명으로 격리하고, 검증된 deterministic rule/strategy package만 후속 단계로 보낸다. 모델·프롬프트·도구·데이터셋·외부 MCP의 provenance, evaluation, drift, rollback, cost/rate limit, human escalation을 기록한다.

---

## 5. P2 — 상용화 확장 전에 준비할 항목

1. Marketplace 판매자 적합성, 성과 산정 표준, 분쟁·환불·라이선스·제재 운영.
2. 기업/파트너 테넌시, delegated administration, data residency, BYOK, enterprise audit export.
3. 다국가·다통화·세금·법인/가계 자산 그래프 및 개인 CFO 확장.
4. 가격·원가·SLA·지원 운영 모델, incident communication template, 고객보호 지표.
5. 독립 보안 진단, 침투 테스트, DR rehearsal, provider outage drill, legal/compliance review.

---

## 6. Claude Code 작업 지침

### 6.1 절대 원칙

1. P0가 미해결인 영역에서 LIVE 실행, 실자산 자격증명, Marketplace 상용 판매를 확장하지 않는다.
2. 단일 대형 Python 파일을 만들지 않는다. 한 모듈은 하나의 명확한 책임·계약·테스트 경계를 가져야 한다.
3. 분석 엔진과 전략 엔진을 혼합하지 않는다. 분석은 `AnalysisPacket`, 전략은 검증 가능한 `StrategyPackage/SignalProposal`, 리스크는 `RiskDecision`, 실행은 `OrderIntent`까지만 책임진다.
4. MCP/LLM/provider adapter는 capability 경계 밖의 권한을 갖지 않는다.
5. 어떤 기능도 문서·계약·테스트·관측성·롤백 계획 없이 완료 처리하지 않는다.

### 6.2 권장 착수 순서

```text
P0 위험 등록부 + 문서 포털/추적성
→ credential/LIVE/PAPER egress 격리 설계 및 테스트
→ OrderIntent·approval·reservation·reconciliation 원자성
→ audit/evidence/recovery rehearsal
→ Foundation vertical slice의 격리 DB 기반 CI
→ frontend/BFF repository mapping과 contract test
→ analysis/strategy/marketplace 확장
```

### 6.3 작업 시작 전 체크리스트

- 이 작업의 canonical 문서와 work package는 무엇인가?
- 코드 저장소·브랜치·모듈 owner는 명확한가?
- tenant, authority, provider capability, data classification이 계약에 있는가?
- 실패·중복·지연·부분체결·provider outage 시 기대 동작이 명시됐는가?
- PAPER/LIVE와 secret egress가 물리적으로 구분되는가?
- 테스트 DB·fixture·CI에서 재현 가능한가?
- acceptance evidence와 rollback 조건이 있는가?

---

## 7. 참조 문서

- `41_superquant_delivery_portfolio_and_prioritization_v1.0.md`
- `71_mihwa_aios_foundation_implementation_work_packages_v1.0.md`
- `72_implementation_specification_depth_standard_v1.0.md`
- `82_red_team_architecture_assessment_v1.0.md` ~ `88_red_team_campaign_and_remaining_unknowns_v1.0.md`
- `89_analysis_intelligence_platform_architecture_v1.0.md`
- `90_investment_engine_separation_and_contract_architecture_v1.0.md`
- `91_visual_strategy_studio_and_chart_rule_engine_specification_v1.0.md`
- `92_enterprise_backend_complete_domain_and_module_map_v1.0.md`
- `93_enterprise_frontend_information_architecture_and_page_map_v1.0.md` ~ `102_user_value_convenience_and_trust_experience_blueprint_v1.0.md`

---

## 8. Release Gate

| 단계 | 이동 조건 |
|---|---|
| internal development | P0 위험 등록부 생성, 격리 테스트 환경, 기본 계약 검증 |
| internal paper | P0-01~05 완료, staging red-team campaign 및 reconciliation evidence |
| user-facing paper | RTC-01~04 및 RTC-06 통과, 고객보호/지원/incident 운영 승인 |
| limited live | Paper 운영 증거, legal/compliance/provider 승인, LIVE 물리 격리와 independent security evidence |
| marketplace commercialization | RTC-05~10, 성과·적합성·판매자·분쟁·법적 책임 게이트 전부 통과 |

이 게이트는 기능 시연이나 문서 완성도로 대체할 수 없다. 증거 기반 승인만 상태를 전진시킨다.
