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
| `append.py` | 조건부 append(`WHERE seq = expected`), 해시 체인 연결 |
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
- **필수 컬럼**: `orders`·`fills`·`positions`·`journal_entries`·`journal_lines`·`risk_decisions`·`performance_*`에
  `fund_id`(NOT NULL)·`portfolio_id`(NOT NULL) 추가. 기존 행은 기본 펀드/포트폴리오로 백필.
- **양시간축**: 위 테이블 중 상태성 테이블에 `valid_from`·`valid_to`·`tx_from`·`tx_to`(TSTZRANGE 권장) 추가, 현재 행은 `tx_to = 'infinity'`.
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
| FA-1 | `entities/contracts/v1.py` + `domain/hierarchy.py` + `domain/defaults.py` + test | — | 계층 불변조건, 기본 엔티티 자동 생성 | 560 |
| FA-2 | 마이그레이션(4테이블) + `adapters/postgres_repository.py` + 통합 | FA-1 | FK·유일성, 교차 테넌트 404 | 500 |
| FA-3 | **소급 마이그레이션 A**: `orders`·`fills`에 `fund_id`/`portfolio_id` NOT NULL + 기본값 백필 + FK | FA-2 | 기존 행 백필 검증, 누락 쓰기 거부 | 400 |
| FA-4 | **소급 마이그레이션 B**: `positions`·`position_journal`·`journal_entries`·`journal_lines`에 동일 컬럼 + 백필 | FA-3 | 원장 대차 불변 유지 | 400 |
| FA-5 | `application/resolve_context.py` + 기존 유스케이스 시그니처 확장(주문·포지션·원장 진입점) | FA-3 | 컨텍스트 없는 쓰기 정적 검사 0건 | 400 |
| FA-6 | 리스크·성과·API 응답에 펀드/포트폴리오 스코프 반영 + 회귀 | FA-5 | 기존 단일계좌 응답 무변경 | 400 |
| FA-7 | `allocation/domain/{policy,average_price}.py` + test | FA-1 | 3정책 정확값, 라운딩 잔여 0 | 440 |
| FA-8 | `allocation/application/allocate_fills.py` + 마이그레이션 + 통합 | FA-7, FA-4 | 배분 합계 == 체결, 원장 연결 | 400 |
| FA-9 | `core/bitemporal.py` + 4종 질의 + test | — | 질의 스냅샷, 겹침 거부 | 300 |
| FA-10 | 소급 마이그레이션 C: 상태성 테이블 양시간축 컬럼 + UPDATE 금지 트리거 | FA-9, FA-4 | UPDATE 시도 실패, 현재 뷰 동일 | 400 |
| FA-11 | `ledger/domain/correction.py` + `positions/domain/restatement.py` + test | FA-10 | 역분개+재기표, 소급 체결 반영 | 460 |
| FA-12 | `application/{ibor_view,abor_snapshot}.py` + 통합 | FA-11 | 마감 스냅샷 불변, 정정 표시 | 400 |
| FA-13 | `core/eventstore/contracts/v1.py` + `append.py`(해시체인·조건부) + test | — | 시퀀스 충돌 거부, 체인 검증 | 400 |
| FA-14 | `projections/{orders,positions,ledger}.py` — 기존 상태를 투영으로 정의 | FA-13 | 투영 == 현재 테이블 | 600 |
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
