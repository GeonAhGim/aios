# L4 AI 연구·전략 생성 계층(Agent Gateway · Strategy Factory · Experiment Ledger · ML Signals) 명세 v1.0

## 0. 문서 메타
- status: Accepted (2026-09-05) — ADR-2026-09-05-A의 실행 명세
- owner role: Chief Architect(원칙·★ 승인), PM(리프 배정)
- depends on: DSL-1~12(AIOS Script 컴파일·해시), BT-1~12·BT-14~18(백테스트·벡터화), L36~L44(검증 파이프라인, F-04 수정 포함),
  PLT-24/28/31/33(인증·테넌트·KeyRing), MP-1~3(리스팅·버전), INVARIANTS I-06·I-07·I-08·I-10·I-11
- implemented by: `src/foundation/ai/**`, `src/foundation/experiments/**`, `src/foundation/ml/**`, `src/api/mcp/**`, `src/api/routers/ai.py`,
  `frontend/apps/web/src/ai/**`
- 리프 접두: **AI**
- 근거 연구: `docs/research/brainstorm_2026-09-03/` (QuantDinger 스코프 토큰·OBaI StrategyDefinition·AgentQuant/coinjure 실패 양식·Quill Risk Guardian)

## 1. 기관급 요구 (왜 "LLM에 물어보기"로는 부족한가)
| 요구 | 내용 | 강제 지점 |
|---|---|---|
| 권한 분리 | AI는 인간 세션 권한을 상속하지 않는다. 닫힌 스코프 토큰, 서버측 revoke, 자기 권한 API 도달 불가 | AI-2~4 (I-06) |
| 결정론 게이트 | 제안은 컴파일·검증·리스크 게이트를 코드로 통과해야 한다. 프롬프트 임계값은 게이트가 아니다 | AI-9·AI-12 (I-07), R-56 |
| 재현성 | 모든 제안·실험·모델은 재현 키(script_hash‖data_lineage‖config‖model_hash)로 불변 기록 | AI-13~15 |
| 공급자 중립 | 클라우드 LLM·로컬 LLM·외부 MCP 클라이언트가 같은 계약으로 붙는다. 테넌트별 비용 상한 | AI-5~7 |
| 얇은 MCP | MCP 서버는 REST가 이미 강제하는 것 이상의 인가·로직을 갖지 않는다 | AI-2 (I-08) |
| 확인 토큰 | PAPER 실행 요청 등 확인이 필요한 도구는 서버측 1회성 토큰으로 미리보기와 실행을 연결 | AI-4 (I-11) |
| 데이터 누수 차단 | ML 신호는 point-in-time 규칙(학습 시점 이후만 사용), 백테스트 계보에서 검증 | AI-19 |

## 2. 모듈 분해 (최소단위, 파일 ≤300줄)
### 2.1 Agent Gateway — `src/foundation/ai/gateway/`, `src/api/mcp/`
| 파일 | 책임 |
|---|---|
| `contracts/v1.py` | `AgentToken{token_id, tenant_id, scopes: frozenset[Scope], allow_instruments, notional_cap, expires_at, paper_only=True}`, `Scope = read|research|propose|paper`, `ConfirmTicket{ticket_id, action_digest, expires_at}` |
| `domain/token_rules.py` | 발급·검증·revoke 규칙(순수). 스코프 상승 불가, `paper_only` 불변, 만료·revoke 즉시 반영 |
| `domain/confirm.py` | 1회성 확인 토큰 규칙(미리보기 digest = 실행 digest, 단일 사용, TTL) |
| `adapters/postgres_token_repository.py` | opaque 해시 토큰 저장(원문 미저장), revoke 목록, 감사 |
| `application/{issue_token,revoke_token,authorize}.py` | 유스케이스. `authorize(token, scope, resource)`가 모든 MCP 도구의 유일한 인가 지점 |
| `src/api/mcp/server.py`, `src/api/mcp/tools_{read,research,propose,paper}.py` | MCP 서버(stdio+HTTP). 각 도구는 REST 유스케이스를 1:1 호출만 한다(로직 0) |
| `src/api/routers/ai.py` | 토큰 발급/회전/revoke, 제안 목록, 실험 조회(인간 세션용, `login_required`) |

### 2.2 모델 공급자 — `src/foundation/ai/providers/`
| 파일 | 책임 |
|---|---|
| `ports/model_provider.py` | `ModelProvider.generate(schema, prompt, budget) -> StructuredOutput` Protocol. 스키마 강제 출력만 |
| `adapters/anthropic_provider.py` | Claude API(구조화 출력, 비용 계측) |
| `adapters/openai_compatible_provider.py` | OpenAI(GPT/Codex 계열)·로컬 LLM(Ollama/vLLM/LM Studio 등 OpenAI 호환 엔드포인트) |
| `adapters/gemini_provider.py` | Google Gemini API(구조화 출력) |
| `adapters/external_agent_provider.py` | **외부 에이전트 채널**: Claude Code·Codex CLI·Gemini CLI 등 어떤 MCP 클라이언트든 에이전트 토큰으로 접속해 read/research/propose/paper 도구를 쓴다. AIOS 안에 모델이 없어도 파이프라인은 완결된다(공급자 아님, 제안 채널) |
| `domain/budget.py` | 테넌트·토큰별 비용 상한·일일 한도(순수) |
| `domain/prompt_registry.py` | 프롬프트 버전·해시(재현 키 구성 요소) |

### 2.3 Strategy Factory — `src/foundation/ai/factory/`
| 파일 | 책임 |
|---|---|
| `contracts/v1.py` | `StrategyProposal{proposal_id, script_source, hypothesis, data_scope{instruments, tf, span}, params, provider_ref, prompt_hash, created_by_token}`, `ProposalOutcome` |
| `domain/proposal_rules.py` | 제안 수용 규칙(순수): 스키마 통과·컴파일 통과·데이터 범위가 커버리지 안·금지 API 없음 |
| `domain/schema.py` | 생성 스키마(JSON Schema): script/hypothesis/data_scope/params — 자유 텍스트 금지 |
| `application/generate_proposal.py` | 공급자 호출 → 스키마 검증 → DSL 컴파일(DSL-12) → 제안 저장 |
| `application/evaluate_proposal.py` | 검증 파이프라인(L36~L44) 호출 → outcome 기록. FAIL이면 종료 |
| `application/promote_to_paper.py` | 확인 토큰(AI-4) + 리스크 게이트 통과 시에만 PAPER 실행 생성(기존 유스케이스 호출) |
| `application/research_tools.py` | read/research 도구 구현(지표 계산·백테스트 실행·실험 조회) — 전부 기존 유스케이스 위임 |

### 2.4 Experiment Ledger — `src/foundation/experiments/`
| 파일 | 책임 |
|---|---|
| `contracts/v1.py` | `Experiment{experiment_id, reproducibility_key, kind: backtest|sweep|walk_forward|paper, inputs_hash, metrics, artifacts, parent_id, created_by}` |
| `domain/lineage.py` | 부모-자식 계보·재현 키 규칙 |
| `adapters/postgres_repository.py` + 마이그레이션 | append-only(WORM 트리거, L0-3 재사용) |
| `application/{record,query,compare}.py` | 기록·조회·비교(에이전트 컨텍스트 공급) |

### 2.5 ML Signals — `src/foundation/ml/`
| 파일 | 책임 |
|---|---|
| `contracts/v1.py` | `FeatureSpec`, `ModelCard{model_id, version, model_hash, train_data_lineage, trained_at, metrics, drift_baseline}` |
| `domain/point_in_time.py` | 학습 시점 이후 데이터만 사용(순수 규칙) — 백테스트에서 강제 |
| `domain/drift.py` | 분포 드리프트 판정(PSI/KS) |
| `ports/{feature_store,model_registry,trainer}.py` | 포트 |
| `adapters/{parquet_feature_store,postgres_model_registry,local_trainer}.py` | 로컬 학습(LightGBM/torch, 라이선스 확인 후)·저장 |
| `application/{train_job,register_model,serve_signal}.py` | 학습 잡(체크포인트·재개), 등록, 지표 서빙 |
| `src/core/indicators/catalog/ml.py` | `ml.<model_id>` 지표로 레지스트리 등록(IND 스펙 준수) |

## 3. 계약 (요지)
- 토큰: opaque 32바이트, 서버는 sha256만 저장. 헤더 `X-AIOS-Agent-Token`. 인간 JWT와 별도 미들웨어(같은 요청에 둘 다 있으면 거부).
- 스코프 의미: read(지표·데이터·커버리지 조회), research(백테스트·스윕 실행, 실험 조회), propose(제안 제출), paper(PAPER 실행 요청 — 확인 토큰 필수).
  LIVE·자금·정책·토큰 관리 스코프는 존재하지 않는다.
- 제안 스키마: `script_source`(AIOS Script, 문법 aios-script-1), `hypothesis`(≤2,000자), `data_scope`, `params`, `expected_regime`(선택). 미지 필드 거부.
- 에러: `AI_SCOPE_DENIED`(403), `AI_TOKEN_REVOKED`(401), `AI_CONFIRM_REQUIRED`(428), `AI_CONFIRM_MISMATCH`(409), `AI_BUDGET_EXCEEDED`(429),
  `AI_PROPOSAL_SCHEMA`(400), `AI_PROPOSAL_COMPILE`(400, DSL 오류 위치 포함), `AI_PROVIDER_UNAVAILABLE`(503).

## 4. 불변조건
- I-06/I-08/I-11 그대로. 추가: **A-1** 제안이 PAPER로 가는 경로는 `promote_to_paper` 하나뿐이며 그 안에서 리스크 게이트 호출이 없으면 테스트가 실패한다(I-10 배선 증명).
  **A-2** 에이전트는 검증 정책 번들을 읽을 수 있으나 쓸 수 없다(권한 API 부재). **A-3** ML 모델은 `train_data_lineage.end < backtest.start`가 아니면 백테스트에서 호출 거부.

## 5. 동시성·멱등성
- 제안 제출: `(token_id, script_hash)` 멱등. 확인 토큰: 단일 사용 조건부 UPDATE. 학습 잡: 체크포인트 조건부 UPDATE, 재개 가능. 실험 원장: append-only.

## 6. 실패 모드
| 실패 | 조치 |
|---|---|
| 공급자 장애/비용 초과 | 429/503, 제안 없음, 원장에 시도 기록 |
| 스키마 우회 시도(자유 코드) | 400, 토큰별 위반 카운터, 임계 초과 시 자동 revoke |
| 확인 토큰 재사용 | 409, 감사 |
| 모델 드리프트 | 지표 결과에 `degraded` 플래그, 신규 PAPER 승격 차단 |

## 7. SLO
- MCP read 도구 p95 300ms, research(백테스트 1개월) ≤5s, 제안 생성 ≤60s(공급자 제외), 학습 잡 진행률 30s 갱신.

## 8. 테스트
- 적대적: 인간 JWT로 MCP 접근·토큰으로 인간 API 접근·스코프 상승·확인 토큰 재사용·자유 코드 제출·리스크 게이트 우회 시도·미래 데이터 모델.
- 계약: 스키마 스냅샷, 공급자 3종 동일 산출물, 실험 재현 키 동일성.

## 9. 리프 목록 (구현 순서 — 선행: DSL-12, BT-10, L42, MP-3)

**구현 원칙(사용자 지시 2026-09-05): 파이프라인 먼저, 모델은 나중에 꽂는다.** 순서는 (a) 게이트웨이·스키마·게이트·실험 원장(AI-1~5, 8~17) → (b) 외부 에이전트 연결 검증: Claude Code·Codex CLI·Gemini CLI가 MCP 클라이언트로 붙어 제안→검증→PAPER까지 도는 E2E(AI-15/16 DoD에 포함) → (c) 내장 공급자 어댑터(AI-6·7·7b, 선택) → (d) ML 신호(AI-18~21). 내장 모델 없이도 (a)(b)만으로 제품 기능이 완결된다.
| 리프 | 파일 | 선행 | DoD | 크기 |
|---|---|---|---|---|
| AI-1 | gateway/contracts/v1.py + 스냅샷 | — | 스키마 | 240 |
| AI-2 | domain/token_rules.py + domain/confirm.py + test | AI-1 | 상승 불가·revoke 즉시·단일 사용 | 400 |
| AI-3 | 라이선스·의존성 확인 문서(`docs/design/AI_DEPENDENCIES_EVAL.md`: MCP SDK·anthropic SDK·LightGBM·torch·ollama 클라이언트 원문) | — | CH-0 형식 | 200 |
| AI-4 | postgres_token_repository + 마이그레이션 + application/{issue,revoke,authorize} + 통합 | AI-2 | opaque 저장, 교차 테넌트 404 | 600 |
| AI-5 | providers/ports + domain/budget + prompt_registry + test | — | 상한 초과 429 | 400 |
| AI-6 | anthropic_provider(구조화 출력, 비용 계측) + 계약 테스트(fake) | AI-5 | 스키마 강제 | 240 |
| AI-7 | openai_compatible_provider(OpenAI/Codex·로컬 LLM) + 계약 테스트(fake) | AI-5 | 동일 산출물 | 240 |
| AI-7b | gemini_provider + 계약 테스트(fake) | AI-5 | 동일 산출물 | 240 |
| AI-8 | factory/contracts + domain/schema + proposal_rules + test | DSL-12 | 자유 코드 거부 | 500 |
| AI-9 | application/generate_proposal(공급자→스키마→컴파일→저장) + 통합 | AI-6, AI-8 | 컴파일 오류 400 위치 포함 | 240 |
| AI-10 | experiments/contracts + lineage + WORM 저장 + 마이그레이션 | — | append-only 증명 | 500 |
| AI-11 | experiments/application/{record,query,compare} + 통합 | AI-10 | 재현 키 동일성 | 300 |
| AI-12 | application/evaluate_proposal(검증 파이프라인 위임) + 통합 | AI-9, L42 | 임계 미달 → FAIL 기록(I-07) | 240 |
| AI-13 | application/promote_to_paper(확인 토큰 + 리스크 게이트) + 적대적 테스트 | AI-4, AI-12 | 게이트 미호출 시 실패(A-1) | 260 |
| AI-14 | application/research_tools(지표·백테스트·실험 위임) + test | AI-11, BT-10 | 위임만, 로직 0 | 260 |
| AI-15 | src/api/mcp/server.py + tools_read/research(얇은 프록시) + 적대적(인간 JWT 거부) | AI-4, AI-14 | I-08 | 600 |
| AI-16 | tools_propose/paper + 확인 토큰 왕복 + 적대적(재사용 409) | AI-13, AI-15 | I-11 | 300 |
| AI-17 | api/routers/ai.py(토큰 관리·제안·실험 조회) + 통합 | AI-4, AI-11 | 교차 테넌트 404 | 260 |
| AI-18 | ml/contracts + point_in_time + drift + test | — | 미래 데이터 거부(A-3) | 500 |
| AI-19 | ml ports + parquet_feature_store + postgres_model_registry + 마이그레이션 | AI-18 | 계보 저장 | 600 |
| AI-20 | local_trainer + train_job(체크포인트) + register_model + 통합 | AI-19, AI-3 | 재개 가능 | 500 |
| AI-21 | serve_signal + indicators/catalog/ml.py(`ml.*` 지표) + 증분=일괄 동일성 | AI-20, IND-1 | IND 스펙 준수 | 400 |
| AI-22 | 프론트 `AiStudioPage.tsx`(공급자 설정·토큰·제안 목록·실험 비교·승격 버튼 확인 흐름) | AI-17 | 화면·negative | 300 |

## 10. 미확정·리스크
- 공급자 SDK·ML 라이브러리 라이선스는 AI-3에서 원문 확인(LightGBM MIT·PyTorch BSD·Ollama MIT로 알려져 있으나 확인 전 반입 금지).
- 클라우드 학습(외부 GPU) 연동은 범위 밖 — `trainer` 포트만 두고 로컬 구현으로 시작.
- 뉴스·소셜·대안 데이터는 데이터 벤더 결정과 함께 사람 결정.
