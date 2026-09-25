# L4 구현 명세: MVP-3 — T1 운영 깊이 (ADR-2026-09-06-B 이연분)

## 0. 문서 메타
- status: draft (spec_hold — MVP-2 closing finding 전까지 착수 금지, task-2746 hold_gate)
- owner role: backend
- supersedes: 없음(신설). ADR-2026-09-06-B "지금은 포트만, 구현은 뒤로" 절의 이연분을 리프로 확정한다.
- depends on:
  - `docs/design/ADR-2026-09-06-B-beyond-t1-target-architecture.md` (D5·D6·D7 — 포트 결정의 원문)
  - `docs/design/ADR-2026-09-09-F-frontier-roadmap.md` (MVP-3 단계 정의, "명세는 MVP-2 closeout 전에 CTO가 작성")
  - `docs/specs/L4_ems_routing_algos_and_tca_v1.0.md` (EM-17 `ports/fix_session.py` — done, 957ae247)
  - `docs/specs/L4_compliance_and_regulatory_v1.0.md` (CM-16 `reporting/ports/report_submitter.py` — done, 8cd7cfe5)
  - `docs/specs/L4_ibor_fund_accounting_and_resilience_v1.0.md` (FA-18 failover, FA-20 backup_verify, FA-22/23 KMS 포트, FA-24 region — done/inflight)
  - `src/foundation/reconciliation/` (기존 단일 커스터디언 대사 — 다중 커스터디언 확장 대상)
- implemented by(파일 경로): 아래 §9 리프 목록의 "파일" 열. 착수 전 전부 미생성.
- verification evidence(테스트 경로): 아래 §9 리프 목록의 "DoD" 열에 지정. 착수 전 전부 미실행.

이 문서는 **명세 초안**이다. 코드 변경 없음(task-2746 spec 필드). MVP-2 closing finding이 기록되기 전까지
어떤 리프도 `assigned` 상태로 전환하지 않는다(hold_gate). ADR-2026-09-09-F §"걸리적거리지 않게 원칙" —
현재 단계 작업과 다음 단계 우선 1 리프는 병행 가능하므로, closing 판정 이후 PM이 우선순위 1개 리프부터
배정한다.

## 1. 기관급 요구 (왜 포트만으로는 부족한가)
ADR-2026-09-06-B는 FIX 회선, 규제 보고 제출, 다중 커스터디언 대사, HA 클러스터 실배포, SOC2 통제 매핑,
GIPS 준거 성과를 "포트·계약만 MVP-1, 어댑터·운영은 MVP-2/3"으로 이연했다. 각 항목이 T1
(Aladdin·Charles River·Bloomberg AIM/EMSX·FlexTrade) 기준에서 요구하는 실제 수준:

| 항목 | T1 기준 요구 | 현재 코드 수준 | 격차 |
|---|---|---|---|
| FIX 회선 | 거래소·브로커와 실제 FIX 4.4/5.0 세션(로그온·시퀀스·재전송·하트비트)으로 주문 전달 | `EM-17 ports/fix_session.py` — 어댑터 없는 포트만(계약 테스트로 로컬 시맨틱만 증명, "실제 벤더 wire 동작은 미검증"이 파일 주석에 명시) | 실제 벤더 세션 어댑터 0개, 재전송·시퀀스 갭 복구 미구현 |
| 규제 보고 제출 | MiFID II RTS 27/28, 국내 자본시장법 거래 보고를 채널(FTP/API/파일)로 규제기관에 제출·확인 수신 | `CM-16 reporting/ports/report_submitter.py` + `application/generate_report.py` — 보고서 생성·해시·불변 저장까지, 제출 채널 어댑터 없음 | 실제 제출 채널 0개, 제출 확인·재제출 정책 없음 |
| 다중 커스터디언 대사 | 여러 수탁기관(증권사·은행·해외 커스터디언) 잔고를 동시에 대사하고 커스터디언 간 이동(in-transit)을 넷팅 | `src/foundation/reconciliation/` — `ReconciliationRun.connection_id: UUID \| None` 단일 연결 전제, 커스터디언 간 이동 항목·넷팅 규칙 없음 | 다중 소스 병합, in-transit 분류, 커스터디언 간 불일치 우선순위 규칙 없음 |
| HA 실배포 | RPO=0/RTO≤5분 실제 클러스터(Postgres 동기 복제 + 자동 페일오버) 운영, 무중단 배포 | `FA-18 db/failover.py`(다중 호스트·읽기전용 감지·재연결) — 코드 적합성만, 실제 Patroni/etcd 클러스터·페일오버 훈련 없음 | 인프라 프로비저닝, 실 페일오버 리허설, 무중단 배포 파이프라인 없음 |
| SOC2 통제 매핑 | SOC2 Type II 통제 항목(접근제어·변경관리·모니터링 등)과 코드/운영 증거의 명시적 매핑 문서 | 없음(개별 통제는 존재하나 SOC2 프레임워크 대응표 없음) | 매핑 문서, 증거 수집 절차 없음 |
| GIPS 준거 성과 | GIPS(Global Investment Performance Standards) 공식(TWR·복합계정) 준거 성과 산출·표기, 검증 가능한 방법론 공시 | `FA-12 application/{ibor_view,abor_snapshot}.py`까지만 — 펀드/포트폴리오 단위 성과는 있으나 GIPS 복합계정·준거 공시 없음 | 복합계정(composite) 모델, GIPS 방법론 공시, 외부 검증 절차 없음 |

## 2. 모듈 분해 (최소단위)
| 파일 경로 | 단일 책임 | 공개 계약(함수/클래스 시그니처) | 의존(포트) | 줄수 상한 | Zone |
|---|---|---|---|---|---|
| `src/foundation/ems/adapters/fix_vendor_session.py` | 실 벤더 FIX 세션 어댑터(1개 벤더) | `class VendorFixSession(FixSessionPort)` | EM-17 `FixSessionPort` | 500 | foundation |
| `src/foundation/ems/adapters/fix_sequence_recovery.py` | 시퀀스 갭 감지·재전송 요청 | `def detect_gap(...) -> GapReport`, `async def request_resend(...)` | 위 어댑터 | 300 | foundation |
| `src/foundation/mandates/reporting/adapters/report_channel_sftp.py` | 보고서 파일 채널 제출(SFTP/파일 드롭) | `class SftpReportSubmitter(ReportSubmitterPort)` | CM-16 `ReportSubmitterPort` | 300 | foundation |
| `src/foundation/mandates/reporting/application/confirm_submission.py` | 제출 확인 수신·재제출 정책 | `async def confirm_submission(...) -> SubmissionStatus` | 위 어댑터 | 260 | foundation |
| `src/foundation/reconciliation/domain/multi_custodian.py` | 다중 커스터디언 항목 분류·in-transit 판정 | `def classify_cross_custodian(...) -> Classification` | 없음(순수) | 400 | foundation |
| `src/foundation/reconciliation/application/reconcile_multi_custodian.py` | 커스터디언별 조회 병합·넷팅 실행 | `async def run_multi_custodian_reconciliation(...) -> ReconciliationRun` | 위 domain, `ports/repository.py` | 400 | foundation |
| `ops/ha/patroni_deploy.md` + `ops/ha/failover_drill.py` | 실 클러스터 배포 절차·페일오버 리허설 스크립트 | `def run_failover_drill(...) -> DrillReport` | FA-18 `db/failover.py` | 300 | ops |
| `docs/compliance/SOC2_CONTROL_MAP.md` | SOC2 통제 ↔ 코드/운영 증거 대응표 | 문서(비코드) | 없음 | — | docs |
| `src/foundation/performance/domain/gips_composite.py` | GIPS 복합계정 모델·TWR 공식 | `class Composite`, `def time_weighted_return(...) -> Decimal` | FA-12 `ibor_view` | 400 | foundation |
| `src/foundation/performance/application/generate_gips_report.py` | GIPS 준거 성과 보고서 생성 + 방법론 공시 | `async def generate_gips_report(...) -> GipsReport` | 위 domain | 300 | foundation |

도메인 규칙(`multi_custodian.py`, `gips_composite.py`)은 I/O 없는 순수 함수/클래스. 벤더 세션·보고 채널·클러스터
배포는 전부 어댑터/운영 스크립트로 분리한다.

## 3. 계약 (Contract)
- `GapReport`, `SubmissionStatus`, `DrillReport`, `GipsReport`는 pydantic DTO. 금액은 `Decimal`, 시각은
  tz-aware UTC, `schema_version` 필드 포함(107번 표준).
- 에러 taxonomy(초안, 어댑터 구현 시 확정):
  - FIX: `FixSequenceGapError`(재시도 가능 — 재전송 요청), `FixLogonRejectedError`(재시도 불가 — 자격 재확인 필요)
  - 보고 제출: `SubmissionChannelUnavailableError`(재시도 가능, 백오프), `SubmissionRejectedError`(재시도 불가 —
    사람 검토 필요)
  - 다중 커스터디언: `CustodianDataStaleError`(재시도 가능), `CrossCustodianAmbiguousError`(재시도 불가 — 사람 배정)
- 미확인 외부 사실(벤더 FIX 명세 세부, 특정 커스터디언 파일 포맷, 규제기관 제출 API 스펙, SOC2 감사기관 요구
  증거 형식)은 착수 시점에 `NotImplementedError` + `# ratchet-allow: <근거>`로 남긴다(CLAUDE.md §3). 이 명세는
  그 근거를 지금 확정하지 않는다 — 벤더·기관 계약이 아직 없다(§10).

## 4. 불변조건·상태기계
- FIX: 세션은 `LOGGED_OUT → LOGGED_ON → LOGGED_OUT` 상태기계. 시퀀스 갭 감지 시 즉시 주문 송신을 중단
  (fail-closed) — EM-17 포트의 로컬 시맨틱과 동일 원칙을 어댑터로 승격.
- 다중 커스터디언: 두 커스터디언 데이터가 상충하면 어느 쪽도 신뢰하지 않고 `MATERIAL_MISMATCH`로
  fail-closed(기존 `Classification` enum 재사용, 신규 상태 추가 금지 — INVARIANTS 위반 방지).
- HA 페일오버: 리스 만료 중 쓰기는 전부 거부(fail-closed), FA-18의 기존 리스 규칙과 일관.
- GIPS: 방법론이 승인 없이 변경되면 이전 기간 성과 재계산은 금지(append-only 원칙, D2/D3 일관) — 방법론
  변경은 새 `Composite` 버전으로만.

## 5. 동시성·멱등성·트랜잭션 경계 (105번 표준)
- 보고서 제출은 멱등키(`report_id` + `schema_version` digest)로 중복 제출을 차단. 제출 상태는 조건부
  UPDATE(`status: PENDING → SUBMITTED`, 이미 `SUBMITTED`면 재제출 거부).
- 다중 커스터디언 대사 실행은 기존 `ReconciliationRun.input_hash` 중복 실행 방지 규칙(REC-004/006, DEDUPED)을
  그대로 재사용 — 커스터디언 조합도 입력 해시에 포함.
- FIX 시퀀스 상태는 세션별 `FOR UPDATE` — 동시 두 프로세스가 같은 세션에 송신하면 하나는 대기.

## 6. 실패 모드와 복구
| 실패 | 감지 방법 | 즉시 조치 | 복구 절차 | 감사 기록 |
|---|---|---|---|---|
| FIX 시퀀스 갭 | 수신 시퀀스 번호 불연속 | 송신 중단(fail-closed) | 재전송 요청(34=2) 후 갭 채움, 실패 시 세션 재로그온 | 갭 구간·재전송 이력 append-only |
| 보고 채널 불명 응답(타임아웃) | 채널 확인 타임아웃 | `PENDING_CONFIRMATION` 유지, 재제출 금지(중복 보고 위험) | 사람이 채널 상태 확인 후 수동 확정 | 타임아웃·확정 이벤트 기록 |
| 커스터디언 간 데이터 상충 | 두 소스 값이 허용오차 초과 | `MATERIAL_MISMATCH`, 해당 항목 블로킹 | 조사(`INVESTIGATING`) → 해결(`RESOLVED`) 워크플로(기존 대사 상태기계 재사용) | 기존 대사 감사 경로 재사용 |
| HA 페일오버 중 쓰기 시도 | 리스 만료 감지(FA-18) | 쓰기 거부 | 신 프라이머리 확정 후 재시도(멱등) | 페일오버 이벤트·리스 만료 기록 |
| GIPS 방법론 오류 발견 | 사후 검증(외부 검증인 또는 내부 재계산) | 해당 복합계정 보고 동결 | 새 방법론 버전으로 재계산, 과거 보고서는 정정 주석과 함께 재발행 | 방법론 버전·정정 이력 append-only |

## 7. 성능·SLO·관측성 (108번 표준)
- FIX 송신 지연: p99 ≤ 50ms(로컬 처리, 벤더 왕복 제외). 메트릭 `ems_fix_send_latency_ms`.
- 보고 제출 처리량: 일 배치 기준 최소 1,000건/시간, 지연 예산 없음(배치성). 메트릭 `reporting_submission_throughput`.
- 다중 커스터디언 대사: 커스터디언 3개 병합 기준 p95 ≤ 5s(대사 1회 실행). 메트릭 `reconciliation_multi_custodian_duration_ms`.
- HA 페일오버: RTO ≤ 5분(ADR-2026-09-06-B D6), 측정은 `failover_drill.py` 리허설 결과로.
- 로그 필드: `trace_id`, `tenant_id`, `component`, `event`, `duration_ms` — 기존 관측성 규약과 동일.

## 8. 테스트 계획
- 단위(순수 규칙): `multi_custodian.py`, `gips_composite.py` — 분류·TWR 공식 정확도.
- 통합(실DB): 대사 실행 병합, 보고 제출 상태 전이, FIX 세션 상태기계.
- 적대적: 시퀀스 갭 위조, 중복 제출 시도, 커스터디언 데이터 변조, 페일오버 중 쓰기 시도, 방법론 무단 변경.
- 계약: 벤더/채널 어댑터가 확정되기 전까지는 포트 계약 테스트(EM-17/CM-16 기존 방식)로 대체.
- 성능: §7 수치에 대한 회귀 테스트 1개 이상.
- 각 리프 최소 negative test 1개(CLAUDE.md §3). Safety/execution/ledger/compliance/data 축(R·L4·LA/LB/LC·FA·CM·EO·DC)에
  해당하는 리프는 D3(적대적 + `INVARIANTS.md` 대조 + `replay_verify` 통과) — 아래 OP-3·OP-6·OP-9는 D3 대상.

## 9. 리프 목록 (구현 순서)
| 리프 ID | 파일 | 선행 리프 | DoD(검증 명령·기대 결과) | 예상 크기 |
|---|---|---|---|---|
| OP-1 | `foundation/ems/adapters/fix_vendor_session.py` — 벤더 1개 FIX 세션 어댑터 | EM-17 | 계약 테스트: `FixSessionPort` 전체 구현, 로그온/로그아웃/시퀀스 재설정 | 500 |
| OP-2 | `foundation/ems/adapters/fix_sequence_recovery.py` + 적대적(위조 갭) | OP-1 | 갭 감지 시 송신 중단, 재전송 후 정상 재개 | 300 |
| OP-3 | `foundation/mandates/reporting/adapters/report_channel_sftp.py` — human_blocked 후보(§10) | CM-16 | 계약 테스트 우선(채널 계약 미확정 시 `NotImplementedError` + ratchet-allow) | 300 |
| OP-4 | `foundation/mandates/reporting/application/confirm_submission.py` + 멱등 재제출 거부 test | OP-3 | 중복 제출 차단, 타임아웃 시 `PENDING_CONFIRMATION` | 260 |
| OP-5 | `foundation/reconciliation/domain/multi_custodian.py` + test | 없음(순수) | in-transit·상충 분류 정확값·경계 | 400 |
| OP-6 | `foundation/reconciliation/application/reconcile_multi_custodian.py` + 통합 + 적대적(데이터 변조) | OP-5 | 3개 커스터디언 병합, `MATERIAL_MISMATCH` fail-closed 증명 | 400 |
| OP-7 | `ops/ha/patroni_deploy.md` — human_blocked 후보(§10, 인프라 프로비저닝) | FA-18 | 배포 절차 문서화, 코드 변경 없음 | — |
| OP-8 | `ops/ha/failover_drill.py` + 리허설 실행 로그 | OP-7, FA-18 | RTO ≤ 5분 측정, 드릴 리포트 저장 | 300 |
| OP-9 | `docs/compliance/SOC2_CONTROL_MAP.md` — human_blocked 후보(§10, 외부 감사기관 계약) | 없음 | 통제 항목 ↔ 코드/운영 증거 대응표, 코드 변경 없음 | — |
| OP-10 | `foundation/performance/domain/gips_composite.py` + test | FA-12 | TWR 공식 정확값, 복합계정 편입·이탈 규칙 | 400 |
| OP-11 | `foundation/performance/application/generate_gips_report.py` + 통합 + 방법론 정정 test | OP-10 | 방법론 버전 고정, 무단 재계산 차단(D3 대상) | 300 |

리프 하나 = 커밋 하나. 위 순서는 착수 순서 권고이며, 각 리프는 독립적으로 CI 통과 가능해야 한다(§9 원칙,
_TEMPLATE.md와 동일). **hold_gate: 이 표의 어떤 리프도 MVP-2 closing finding이 PM 결정으로 기록되기 전에는
`assigned`로 전환하지 않는다.**

## 10. 미확정·리스크
- **OP-3(규제 보고 제출 채널)**: 실제 제출 대상 채널(국내 금융투자협회·거래소 API 스펙, 파일 포맷, 인증 방식)이
  계약·문서로 확정되지 않았다. human_blocked 후보 — 사람이 채널 계약을 확보하기 전까지 어댑터는 포트 계약
  테스트로 대체하고 `NotImplementedError`로 정직하게 표시한다.
- **OP-7(HA 실배포)**: 실제 인프라(클러스터 호스트·네트워크·Patroni/etcd 프로비저닝)가 필요하다. human_blocked
  후보 — 사람이 인프라를 확보·승인하기 전까지 코드 변경 없이 배포 절차 문서만 작성한다.
- **OP-9(SOC2 매핑)**: 외부 감사기관과의 계약·감사 범위 확정이 선행되어야 실제 증거 수집이 의미를 갖는다.
  human_blocked 후보 — 사람이 감사기관을 확정하기 전까지 매핑 문서(어떤 통제를 어떤 코드/운영으로 충족할
  계획인지)만 작성한다.
- **OP-1(FIX 벤더 어댑터)**: 어느 벤더(거래소 직결·브로커 FIX 게이트웨이)를 1차 대상으로 할지 미확정. 벤더
  선정은 사람 결정(계약·비용) — 선정 전까지는 벤더 중립적 계약 테스트만 작성 가능.
- **OP-6(다중 커스터디언)**은 코드만으로 완결 가능(외부 계약 불필요) — 기존 대사 상태기계·`Classification`
  enum을 재사용하므로 신규 상태 추가로 인한 INVARIANTS 위반 위험 없음.
- **OP-10/OP-11(GIPS)**은 방법론 자체는 코드로 구현 가능하나, 실제 "GIPS 준거" 표기를 대외 공시하려면 외부
  검증(3rd-party verification)이 필요할 수 있다 — 이는 이 명세의 범위 밖(사람/법무 결정).
