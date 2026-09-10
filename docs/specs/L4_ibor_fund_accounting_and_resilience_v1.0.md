# L4 엔티티 계층·양시간축 장부(IBOR/ABOR)·이벤트 재현·운영 연속성 명세 v1.0

## 0. 문서 메타
- status: Accepted (2026-09-06) — ADR-2026-09-06-B D1·D2·D3·D6·D7의 실행 명세
- owner role: Chief Architect(구조 축·소급 마이그레이션 승인), PM(리프 배정)
- depends on: LC-1~17(원장), LB-1~19(포지션), L4-01~29(OMS), L0-3(WORM), EO-01~06(리스·소유권), PLT-28(테넌트 컨텍스트)
- implemented by: `src/foundation/entities/**`, `src/foundation/ledger/**`(확장), `src/foundation/positions/**`(확장),
  `src/core/eventstore/**`, `src/db/migrations/**`, `scripts/replay_verify.py`
- 리프 접두: **FA**
- **용어 주의(CA 2026-09-06 재검토)**: 저장소에 "account"가 이미 세 뜻으로 쓰인다 — (a) `ledger_account`는 복식부기 **계정과목**
  (`src/db/migrations/versions/4a1d0c0de005_ledger_core.py`), (b) 거래소 계좌는 `portfolio.venue_account_ref`, (c) 이 명세의
  `sub_account`는 **배분 단위**다. 세 개를 섞지 말 것. 기존 `src/core/portfolio/engine.py`는 단일 포트폴리오 전제이므로 FA-6에서
  `portfolio_id` 스코프를 받도록 확장한다(신규 컨텍스트를 만들지 않는다).
- **성격**: 이 명세의 FA-1~8은 **기존 완료 리프에 대한 소급 구조 변경**이다. 지금 하지 않으면 나중에 제품을 세우고 재구축해야 한다.

## 1. 기관급 요구
| 요구 | 내용 | 강제 지점 |
|---|---|---|
| 다법인·다펀드 | 한 테넌트가 여러 법인·펀드·포트폴리오를 운용, 주문·포지션·원장·성과가 전부 그 축으로 분리 | FA-1~6 |
| 블록 주문 배분 | 하나의 주문을 여러 sub_account에 평균단가로 배분, 배분 후 각 계좌 장부에 반영 | FA-7~8 |
| IBOR/ABOR 분리 | "지금 아는 진실"(IBOR, 정정 반영)과 "그때 알던 장부"(ABOR, 마감 스냅샷)를 같은 데이터로 재구성 | FA-9~12 |
| 결정론적 재현 | 임의 날짜 이벤트를 재생하면 그날 투영이 바이트 동일 | FA-13~16 |
| 운영 연속성 | RPO=0·RTO≤5분에 맞는 코드(짧은 트랜잭션·멱등 재시도·페일오버 인지) | FA-17~21 |
| 데이터 주권·키 | 테넌트 리전 태그, KMS/HSM 포트 | FA-22~24 |

## 2. 모듈 분해 (파일 ≤300줄)
### 2.1 엔티티 계층 — `src/foundation/entities/`
| 파일 | 책임 |
|---|---|
| `contracts/v1.py` | `LegalEntity{entity_id, tenant_id, name, jurisdiction, region_tag}`, `Fund{fund_id, entity_id, base_currency, mandate_ref, inception}`, `Portfolio{portfolio_id, fund_id, venue_account_ref}`, `SubAccount{sub_account_id, portfolio_id, owner_ref}` |
| `domain/hierarchy.py` | 계층 불변조건(순수): 상위 없는 하위 금지, 통화 상속, 폐쇄 규칙 |
| `domain/defaults.py` | 개인 사용자용 기본 법인·펀드·포트폴리오 자동 생성 규칙(UX 무변경 보장) |
| `adapters/postgres_repository.py` + 마이그레이션 | 4개 테이블 + FK |
| `application/{create_fund,close_fund,resolve_context}.py` | 유스케이스. `resolve_context(request)`가 모든 쓰기의 단일 진입 |

### 2.2 배분 — `src/foundation/allocation/`
| 파일 | 책임 |
|---|---|
| `domain/policy.py` | `pro_rata\|fixed_weight\|manual` 배분 규칙(순수), 잔여 수량 라운딩(원장 라운딩 규칙 재사용) |
| `domain/average_price.py` | 부분체결 누적 평균단가 배분(순수, Decimal 무손실) |
| `application/allocate_fills.py` + 마이그레이션 | 체결 → sub_account 배분, 원장 분개 연결 |

### 2.3 양시간축 장부 — `src/foundation/ledger/`·`positions/` 확장
| 파일 | 책임 |
|---|---|
| `core/bitemporal.py` | `valid_time`·`transaction_time` 부여·질의 규칙(순수), `as_of(valid, tx)` 조합 4종 질의 |
| `ledger/domain/correction.py` | 정정 분개(역분개 + 재기표), UPDATE 금지 |
| `positions/domain/restatement.py` | 포지션 정정(늦게 도착한 체결·기업행위 소급) |
| `application/{ibor_view,abor_snapshot}.py` | IBOR 현재 뷰 / ABOR 마감 스냅샷(불변 저장) |

### 2.4 이벤트 스토어·재현 — `src/core/eventstore/`
| 파일 | 책임 |
|---|---|
| `contracts/v1.py` | `DomainEvent{stream_id, seq, type, payload, occurred_at, recorded_at, causation_id, correlation_id, hash, prev_hash}` |
| `append.py` | 조건부 append(`WHERE seq = expected`) + **기존 해시체인 승격**: `core/db/append_only.py`(L0-3)와 `ledger/domain/hash_chain.py`(LC-3)를 재사용한다 — 새 체인 구현 금지(ADR-2026-09-06-B D3 "새로 만드는 것이 아니라 규칙으로 승격") |
| `replay.py` | 스트림 재생 → 투영 재구축(순수 함수 조합) |
| `projections/{orders,positions,ledger}.py` | 투영 정의(현재 테이블과 동일 결과) |
| `scripts/replay_verify.py` | 임의 날짜 재생 후 투영 == 저장 상태 바이트 비교(야간 CI) |

### 2.5 운영 연속성 — `src/core/db/`·`ops/`
| 파일 | 책임 |
|---|---|
| `db/session_policy.py` | 트랜잭션 상한(기본 2초) 강제, 초과 시 예외 + 메트릭 |
| `db/failover.py` | 다중 호스트 접속·읽기 전용 승격 감지·재연결 백오프 |
| `ops/backup_verify.py` | PITR 복구 리허설(임시 인스턴스 복구 → 무결성 질의) |
| `docs/ops/DR_RUNBOOK.md` | RPO/RTO 목표·페일오버·복구 절차 |

### 2.6 키·주권
| 파일 | 책임 |
|---|---|
| `core/security/kms_port.py` + `adapters/{local_keyring,kms_stub}.py` | KMS/HSM 포트(기존 KeyRing은 local 어댑터로 이동) |
| `entities/domain/region.py` | 테넌트·법인 리전 태그, 저장 위치 정책 판정 |

## 3. 계약 (요지)
- **필수 컬럼**(실제 테이블명 — 감사 2026-09-06 정정): `orders`·`fills`·`pos_snapshot`·`pos_journal`·`pos_account`·`ledger_journal_entry`·`ledger_posting_line`·`ledger_account`·`risk_decisions`·`performance_*`에
  `fund_id`(NOT NULL)·`portfolio_id`(NOT NULL) 추가. 기존 행은 기본 펀드/포트폴리오로 백필.
- **양시간축(감사 2026-09-06 범위 축소)**: WORM 트리거가 이미 걸린 append-only 테이블(`order_events`·`fills`·`pos_journal`·`ledger_journal_entry`·`ledger_posting_line`·`risk_decision`)은 물리적으로 UPDATE가 불가하므로 `tx_from`/`tx_to`를 추가하지 않는다(불필요한 의식). 양시간축은 **투영 테이블에만** 적용한다: `pos_snapshot`·`ledger_balance`·레거시 `positions`. 선행 사례로 `md_symbol_alias`(`4a1d0c0de007:91`)가 이미 `valid_from`/`valid_to` + EXCLUDE를 쓰되 **UPDATE로 구간을 닫으므로**(SCD-2) FA-10에서 append-only로 전환할지 SCD-2를 예외로 인정할지 결정한다.
- **이벤트**: 모든 상태 전이는 이벤트 append 후 투영 갱신을 **같은 트랜잭션**에서 수행한다(outbox 패턴과 병존).
- 에러: `FA_HIERARCHY_VIOLATION`(400), `FA_ALLOCATION_RESIDUAL`(409, 배분 잔여 불일치), `FA_BITEMPORAL_OVERLAP`(409),
  `FA_REPLAY_MISMATCH`(500, CI 전용), `FA_TXN_TOO_LONG`(500), `FA_REGION_DENIED`(403).

## 4. 불변조건
- **FA-A1** `fund_id`·`portfolio_id` 없는 금전·주문·포지션 쓰기는 DB 제약으로 거부된다.
- **FA-A2** 상태성 테이블에 UPDATE/DELETE 금지(정정은 새 행 + `tx_to` 마감). 트리거로 강제.
- **FA-A3** 배분 합계 == 원 주문 체결 수량(잔여 0), 평균단가 가중합 오차 ≤ 1 최소단위.
- **FA-A4** 임의 날짜 재생 결과 == 저장된 투영(야간 CI, 불일치 시 릴리스 차단).
- **FA-A5** 어떤 트랜잭션도 2초를 넘지 않는다(HA 페일오버 중 잠금 지속 방지).

## 5. 동시성·멱등성
- 이벤트 append: `(stream_id, seq)` 유일 + 조건부 삽입. 배분: `(order_id)` 단위 advisory lock + 멱등. ABOR 스냅샷: `(fund_id, as_of_date)` 유일.

## 6. 실패 모드
| 실패 | 조치 |
|---|---|
| 페일오버 중 쓰기 | 읽기전용 감지 → 재연결·재시도(멱등), 리스 만료 시 로컬 정지 |
| 늦게 도착한 체결 | 포지션·원장 정정(새 행), 영향 받은 ABOR 스냅샷에 정정 표시 |
| 재생 불일치 | 릴리스 차단 + 원인 이벤트 구간 보고 |
| 배분 잔여 | 409, 수동 처리 큐(운영자 승인) |

## 7. SLO
- 이벤트 append p95 20ms, 1일치 재생 ≤ 5분(10만 이벤트), ABOR 스냅샷 생성 ≤ 60초/펀드, 페일오버 후 첫 성공 쓰기 ≤ 60초.

## 8. 테스트
- 적대적: 계층 위반 쓰기, UPDATE 시도, 배분 잔여 조작, 교차 펀드 조회, 2초 초과 트랜잭션, 페일오버 중 중복 주문.
- 계약: 4종 양시간축 질의 스냅샷, 재생 동등성, 기본 계층 자동 생성 후 기존 단일 계좌 UX 회귀.

## 9. 리프 목록 (구현 순서 — **FA-1~8은 최우선, 다른 축보다 먼저**)
| 리프 | 파일 | 선행 | DoD | 크기 |
|---|---|---|---|---|
| **FA-0a** | **소급 정정: `tenant_id` FK 재지정** — 19개 테이블이 `tenant_id UUID REFERENCES users(user_id)`로 선언돼 있다(감사 2026-09-06: `4a1d0c0de004`·`4a1d0c0de005`·`b8d5f2a1c3e4`·`c7d4e1a9f052`·`c7e6a3b2d4f5`·`d8e8e4ba2365`·`6e5baa1c7a55`·`f2b8e5d1a734`·`a1f3c9d6b8e2`·`84b7d0faf14f`·`e91a4c2b7d63`·`4453afe74725`·`4a1d0c0de008`·`4a1d0c0de009`). 실제 `tenant` 테이블(`f4a6b8c0d2e4`)로 재지정하고, `a7c3d9e1f2b4` 트리거의 `v_tenant <> NEW.user_id` 비교도 함께 고친다. 지금은 `tenant.id = users.user_id` 시딩 때문에만 동작하며, ORGANIZATION 테넌트가 생기는 순간 전부 깨진다 | — | 19 FK 재지정 + 트리거 수정, 조직 테넌트 픽스처로 통합 테스트 | 460 |
| **FA-0b** | **소급 정정: 단일 포트폴리오 전제 제거** — `portfolio_mandate UNIQUE (tenant_id)`(`d8e8e4ba2365:45`) 드롭, `(tenant_id, portfolio_id)`로 교체. `src/foundation/mandates/ports/repository.py:20`의 같은 전제도 수정 | FA-0a | 한 테넌트에 포트폴리오 2개 생성 통합 테스트 | 300 |
| **FA-0c** | **키 문법 정정 A: `ledger_account.account_code`** — 현재 `"USER:{uuid}:{sub}" | "PLATFORM:{NAME}"` 문자열에 계층이 인코딩돼 있고 UNIQUE다(`4a1d0c0de005:81`, `domain/chart_of_accounts.py`). 문자열 문법 대신 `(entity_id, fund_id, portfolio_id, account_type)` 컬럼으로 계층을 옮기고 `account_code`는 표시용 파생값으로 강등. **이 항목이 소급 작업 중 가장 비싸며, 하지 않으면 포트폴리오 2개가 같은 sub-account 유형을 쓸 때 UNIQUE 충돌** | FA-0a, FA-2 | 기존 코드 파싱 회귀 통과, 2포트폴리오 동일 유형 계정 생성 성공 | 500 |
| **FA-0d** | **키 문법 정정 B: `pos_snapshot.position_key`** — `VARCHAR(200) PRIMARY KEY`이며 중앙 생성자가 없고 호출자가 문자열을 만든다(`record_fill.py`·`rebuild_snapshot.py`·`record_funding_fee.py`). `domain/position_key.py` 중앙 생성자를 만들고 `portfolio_id`를 키 구성요소로 편입, `pos_journal UNIQUE (position_key, sequence_no)`도 함께 검토 | FA-0a | 교차 포트폴리오 키 충돌 적대적 테스트, 호출자 전부 중앙 생성자 경유 정적 검사 | 460 |
| FA-1 | `entities/contracts/v1.py` + `domain/hierarchy.py` + `domain/defaults.py` + test | — | 계층 불변조건, 기본 엔티티 자동 생성 | 560 |
| FA-2 | 마이그레이션(4테이블) + `adapters/postgres_repository.py` + 통합. **`legal_entity.tenant_id`는 반드시 `tenant(tenant_id)`를 FK한다** — `users(user_id)`를 FK하면 ADR-2026-09-06-E가 FA-0a를 만든 그 결함의 재생산이다 | FA-1, **FA-0a** | FK 대상 테이블이 `tenant`임을 마이그레이션 정적 검사로 단언, 유일성, 교차 테넌트 404 | 500 |
| FA-2a | **소급 교정**: 이미 병합된 `e6b1d94a7c3f_fa2_entities_hierarchy.py:53`의 `legal_entity.tenant_id REFERENCES users(user_id)`를 `tenant(tenant_id)`로 바꾸는 마이그레이션 + 기존 행 재매핑 | FA-2, FA-0a | 교정 후 `users`를 FK하는 `tenant_id` 컬럼이 `legal_entity`에 0건, 하위 4테이블 계층 질의 회귀 통과 | 220 |
| FA-3 | **소급 마이그레이션 A**: `orders`·`fills`에 `fund_id`/`portfolio_id` NOT NULL + 기본값 백필 + FK. OMS 5개 테이블(`order_events`·`fills`·`order_command_outbox`·`order_idempotency`·`provider_event_inbox`)은 `tenant_id`가 없고 `orders`로 간접 격리되므로 **조인 유지 여부를 이 리프에서 결정**한다 | FA-2, FA-0c, FA-0d | 기존 행 백필 검증, 누락 쓰기 거부 | 400 |
| FA-4 | **소급 마이그레이션 B**: `pos_snapshot`·`pos_journal`·`pos_account`(LB-8)·`ledger_journal_entry`·`ledger_posting_line`(LC-6)에 동일 컬럼 + 백필 | FA-3, LB-8, LC-6 | 원장 대차 불변 유지 | 400 |
| FA-5 | `application/resolve_context.py` + 기존 유스케이스 시그니처 확장(주문·포지션·원장 진입점) | FA-3 | 컨텍스트 없는 쓰기 정적 검사 0건 | 400 |
| FA-6 | 리스크·성과·API 응답에 펀드/포트폴리오 스코프 반영 + 회귀 | FA-5 | 기존 단일계좌 응답 무변경 | 400 |
| FA-7 | `allocation/domain/{policy,average_price}.py` + test | FA-1 | 3정책 정확값, 라운딩 잔여 0 | 440 |
| FA-8 | `allocation/application/allocate_fills.py` + 마이그레이션 + 통합 | FA-7, FA-4 | 배분 합계 == 체결, 원장 연결 | 400 |
| FA-9 | `core/bitemporal.py` + 4종 질의 + test | — | 질의 스냅샷, 겹침 거부 | 300 |
| FA-10 | 소급 마이그레이션 C: **투영 테이블만**(`pos_snapshot`·`ledger_balance`·`positions`) 양시간축 컬럼 + UPDATE 금지 트리거. `md_symbol_alias` SCD-2 처리 결정 포함 | FA-9, FA-4 | UPDATE 시도 실패, 현재 뷰 동일 | 400 |
| FA-11 | `ledger/domain/correction.py` + `positions/domain/restatement.py` + test | FA-10 | 역분개+재기표, 소급 체결 반영 | 460 |
| FA-12 | `application/{ibor_view,abor_snapshot}.py` + 통합 | FA-11 | 마감 스냅샷 불변, 정정 표시 | 400 |
| FA-13 | `core/eventstore/contracts/v1.py` + `append.py`(해시체인·조건부) + test. **감사 2026-09-06: 도메인 테이블은 이미 append-only 해시체인이다 — 진짜 공백은 `src/core/event_bus/`가 아무것도 영속화하지 않는 것**이므로 이벤트 버스 durability를 이 리프에 포함한다 | — | 시퀀스 충돌 거부, 체인 검증 | 400 |
| FA-14 | `projections/{orders,positions,ledger}.py` — 기존 `order_events`(OMS)·`pos_journal`(LB-5)·`ledger_journal_entry`(LC)를 이벤트 원천으로 삼아 투영 정의(새 이벤트 테이블 신설 금지) | FA-13, LC-3, LB-5 | 투영 == 현재 테이블 | 600 |
| FA-15 | `replay.py` + `scripts/replay_verify.py` + 야간 CI 훅 | FA-14 | 1일 재생 바이트 동일 | 400 |
| FA-16 | 기존 쓰기 경로에 이벤트 append 삽입(같은 트랜잭션) + 적대적(이벤트 없는 상태 변경 탐지) | FA-14 | 무이벤트 상태 변경 0건 | 500 |
| FA-17 | `db/session_policy.py`(2초 상한) + 위반 탐지 테스트 | — | 장기 트랜잭션 예외 | 240 |
| FA-18 | `db/failover.py`(다중 호스트·읽기전용 감지·재연결) + 통합(모의 페일오버) | FA-17 | 페일오버 후 60초 내 쓰기 복구 | 300 |
| FA-19 | 멱등 재시도 전수 점검(모든 쓰기 유스케이스) + 정적 검사 | FA-18 | 비멱등 쓰기 0건 | 300 |
| FA-20 | `ops/backup_verify.py`(PITR 복구 리허설) + 스케줄 | FA-18 | 복구 후 무결성 질의 통과 | 300 |
| FA-21 | `docs/ops/DR_RUNBOOK.md` + 페일오버 훈련 절차 | FA-20 | 절차·목표(RPO0/RTO5분) 문서화 | 200 |
| FA-22 | `core/security/kms_port.py` + `local_keyring` 어댑터 이전 + test | PLT-31 | 기존 키 동작 무변경 | 300 |
| FA-23 | `kms_stub` 어댑터 + 회전·감사 계약 테스트 | FA-22 | 포트 계약 동일 | 240 |
| FA-24 | `entities/domain/region.py` + 저장 위치 정책 + 적대적(타 리전 저장 거부) | FA-2 | 리전 태그 강제 | 260 |

## 10. 미확정·리스크
- Postgres HA 실배포(Patroni·복제 토폴로지)는 MVP-2 운영 작업. 이 명세는 코드 적합성까지만 강제한다.
- 소급 마이그레이션(FA-3·4·10)은 데이터가 적은 지금이 최적 시점이다. 지연될수록 비용이 커진다는 점을 PM은 배정 우선순위에 반영한다.
- 다중 커스터디언 대사·GIPS 성과 보고는 별도 리프(MVP-2).

## 11. 진행 현황 (자동 생성)

<!-- spec-status:begin (auto-generated by C:/aios/pm/spec_status.py — do not edit) -->
갱신 2026-09-10T08:26:42+00:00 · 총 29 · done 21 · inflight 8 · untouched 0 · hold 0 · 재오픈 21 · 깊이(done) {'D2': 2, 'D3': 8, 'D?': 11}

| ID | 리프 | 상태 | done task | 열린 task | depth | commit |
|---|---|---|---|---|---|---|
| FA-0a | 소급 정정: tenant_id FK 재지정 — 19개 테이블이 tenant_id UUID REFERENCES users(use | done | 1987,1988,2086 | 3015,3017,3022 |  | 8d06215e |
| FA-0b | 소급 정정: 단일 포트폴리오 전제 제거 — portfolio_mandate UNIQUE (tenant_id)(d8e8e4ba2 | done | 1941 | 3016 |  | 3b290ee |
| FA-0c | 키 문법 정정 A: ledger_account.account_code — 현재 "USER:{uuid}:{sub}" | done | 1942 | 3031 |  | 4c925cf4 |
| FA-0d | 키 문법 정정 B: pos_snapshot.position_key — VARCHAR(200) PRIMARY KEY이며 중앙 생 | done | 1943,2543,2545 | 3032,3034,771991202 |  |  |
| FA-1 | entities/contracts/v1.py + domain/hierarchy.py + domain/defaults.py +  | done | 2035,2067,2431 | 3002,3113 | D2 | 6f9dd72d |
| FA-2 | 마이그레이션(4테이블) + adapters/postgres_repository.py + 통합. legal_entity.tena | done | 1704,2431 | 3005,3030 |  | 6f9dd72d |
| FA-2a | 소급 교정: 이미 병합된 e6b1d94a7c3f_fa2_entities_hierarchy.py:53의 legal_entity. | done | 1747,1906 | 3007,3011 |  | 2ea05de |
| FA-3 | 소급 마이그레이션 A: orders·fills에 fund_id/portfolio_id NOT NULL + 기본값 백필 + FK | done | 1709 | 3008 |  | 5a2ddf6 |
| FA-4 | 소급 마이그레이션 B: pos_snapshot·pos_journal·pos_account(LB-8)·ledger_journal | done | 1794,2051,3313 | 3010 | D3 | 9c9ff68 |
| FA-5 | application/resolve_context.py + 기존 유스케이스 시그니처 확장(주문·포지션·원장 진입점) | done | 1709,1795,1925 | 3009,3013 |  | 0765602 |
| FA-6 | 리스크·성과·API 응답에 펀드/포트폴리오 스코프 반영 + 회귀 | done | 1944 | 2629,3033 |  | df84bd55 |
| FA-7 | allocation/domain/{policy,average_price}.py + test | done | 1708 | 3006 |  | 2c0377e |
| FA-8 | allocation/application/allocate_fills.py + 마이그레이션 + 통합 | done | 1796,1989 | 3012,3014 | D2 | 8c11e66 |
| FA-9 | core/bitemporal.py + 4종 질의 + test | done | 2051,2130,2402 | 2905,3003,3028 | D3 | 20e89594 |
| FA-10 | 소급 마이그레이션 C: 투영 테이블만(pos_snapshot·ledger_balance·positions) 양시간축 컬럼 +  | done | 2099,2122,3023 | 3020,3022,3026 | D3 | ea4fda65 |
| FA-11 | ledger/domain/correction.py + positions/domain/restatement.py + test | done | 2058,2059 | 3024 | D3 | 83868239 |
| FA-12 | application/{ibor_view,abor_snapshot}.py + 통합 | done | 2059 | 3025 | D3 | 83868239 |
| FA-13 | core/eventstore/contracts/v1.py + append.py(해시체인·조건부) + test. 감사 2026- | done | 1703,2050 | 3004 |  | 165dcdb0 |
| FA-14 | projections/{orders,positions,ledger}.py — 기존 order_events(OMS)·pos_jo | done | 2061,2115,2173 | 3018 | D3 | 0610a82f |
| FA-15 | replay.py + scripts/replay_verify.py + 야간 CI 훅 | done | 2122,2173,2394 | 3019 | D3 | 65b21f20 |
| FA-16 | 기존 쓰기 경로에 이벤트 append 삽입(같은 트랜잭션) + 적대적(이벤트 없는 상태 변경 탐지) | done | 2173,2406,2432 | 3021,3029 | D3 | 51cb0bef |
| FA-17 | db/session_policy.py(2초 상한) + 위반 탐지 테스트 | inflight |  | 2674 |  |  |
| FA-18 | db/failover.py(다중 호스트·읽기전용 감지·재연결) + 통합(모의 페일오버) | inflight |  | 2675 |  |  |
| FA-19 | 멱등 재시도 전수 점검(모든 쓰기 유스케이스) + 정적 검사 | inflight |  | 2676 |  |  |
| FA-20 | ops/backup_verify.py(PITR 복구 리허설) + 스케줄 | inflight |  | 2677 |  |  |
| FA-21 | docs/ops/DR_RUNBOOK.md + 페일오버 훈련 절차 | inflight |  | 2678 |  |  |
| FA-22 | core/security/kms_port.py + local_keyring 어댑터 이전 + test | inflight |  | 2679 |  |  |
| FA-23 | kms_stub 어댑터 + 회전·감사 계약 테스트 | inflight |  | 2680 |  |  |
| FA-24 | entities/domain/region.py + 저장 위치 정책 + 적대적(타 리전 저장 거부) | inflight |  | 2681 |  |  |
<!-- spec-status:end -->
