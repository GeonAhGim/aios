# Structured Logging and Observability Field Standard v1.0

> 상태: **Mandatory cross-cutting standard.** 73~81번(Foundation L3) 각 문서의 §10
> "Operations and rollout"이 SLI/SLO·alert 이름을 도메인별로 정의했지만, **그 값들이
> 실제로 어떤 로그/메트릭 필드에서 나오는지**는 도메인마다 다시 정의해야 했다. 이
> 문서는 모든 도메인이 공유할 필드 스키마 하나를 고정한다.
>
> 관련: `07_logging_config_v1.3.md`(기존 로깅 포맷), `82_red_team_architecture
> _assessment_v1.0.md` RT-08(간접 데이터 유출 — trace/log/metric label을 통한 secret
> 유출), `35_superquant_service_capability_blueprint_v1.0.md` §3.5(raw secret/내부
> 프롬프트 반환 금지).
>
> 작성일: 2026-09-02

---

## 1. 기존 07번과의 관계 — 대체가 아니라 상위 확장

07번 문서는 초기 MVP 범위(로그 포맷, `risk_policy.yaml`, Secrets 관리)를 정의했다.
이 문서는 07번을 대체하지 않고, 34/35번이 새로 요구하는 **cross-domain 관측성**(모든
서비스가 같은 필드로 상관관계를 추적할 수 있어야 함)을 위해 07번의 로그 포맷 위에
얹는 확장 계층이다. 07번의 필드가 이 문서와 충돌하면 이 문서가 우선한다(더 최신,
더 넓은 범위).

---

## 2. 모든 구조화 로그 라인의 필수 필드

72번 §3의 command envelope(`command_id`, `idempotency_key`, `tenant_id`,
`actor_subject_id`, `trace_id`, `occurred_at`, `schema_version`)를 처리하는 코드
경로는, 그 요청과 관련된 **모든** 로그 라인에 다음을 포함한다:

| 필드 | 타입 | 규칙 |
|---|---|---|
| `trace_id` | UUID string | 요청 진입점(API gateway/router)에서 생성하거나 전파받고, 하위 모든 호출에 그대로 전달. 새로 생성하지 않는다. |
| `tenant_id` | UUID string \| `null` | 인증 전 단계(예: 로그인 시도 실패)는 `null` 허용, 그 외 필수 |
| `actor_subject_id` | UUID string \| `"system"` | 배치/워커가 스스로 트리거한 경우 `"system"` 고정값 |
| `command_id` 또는 `query_id` | UUID string \| `null` | command 경로만 필수, read 경로는 선택 |
| `component` | string | `<context>.<layer>` 형식 고정, 예: `foundation.trust.application` |
| `event` | string | snake_case 동사구, 예: `membership_granted`, `consent_evaluation_denied` — §4 참조 |
| `level` | enum | `debug/info/warn/error` — `critical`은 alert 정의(§5)에서만 사용, 로그 레벨로는 안 씀 |
| `duration_ms` | number \| `null` | command/query 핸들러를 감싸는 경로만, 하위 개별 쿼리는 선택 |

### 2.1 절대 포함하지 않는 필드 (RT-08 직결)

- 원문 secret, provider API key/토큰, 복호화된 credential — `secret_ref`(opaque
  reference)만 허용.
- 사용자 원문 답변(suitability answers), 원문 disclosure 본문 — 73번 §8과 동일
  원칙, `answers_ciphertext_ref`만 허용.
- 다른 tenant의 식별자가 우연히 섞인 배치/집계 로그 — 로그 한 줄은 항상 하나의
  `tenant_id`에 귀속된다(교차 테넌트 집계는 로그가 아니라 명시적 read model에서).
- provider가 반환한 raw payload 전체 — 필요한 필드만 추출해서 로깅, `raw_payload`
  같은 필드명으로 통째로 덤프하지 않는다.

이 절은 82번 RT-08의 "opaque ref도 log/trace/analytics/backup으로 샐 수 있다"는
지적에 대한 실행 규칙이다 — opaque ref 자체는 허용하되, ref가 가리키는 원문을
로거에 넘기는 코드를 만들지 않는다.

---

## 3. 메트릭 네이밍 규칙

```text
aios.<context>.<subject>.<verb_or_measure>[_total|_seconds|_bytes]
```

| 예시 | 타입 | 의미 |
|---|---|---|
| `aios.foundation_trust.membership_grant.count_total` | counter | 성공/실패 무관 총 시도 수 (label: `outcome=success\|denied\|error`) |
| `aios.foundation_trust.status_read.duration_seconds` | histogram | 73번 §10의 p95 <300ms SLO 산출 근거 |
| `aios.foundation_trust.projection_lag.seconds` | gauge | 73번 §10의 projection lag <60s SLO 산출 근거 |
| `aios.foundation_paper_control.order_intent.count_total` | counter | label: `mode=paper\|live_blocked`(105번 표준의 `ConcurrencyConflictError`처럼, 차단된 시도도 반드시 관측 가능해야 함 — RT-02의 "PAPER 경계 우회" 조기 탐지) |

**규칙**: 어떤 L3 문서든 §10(Operations)에서 SLI를 서술할 때, 그 SLI를 산출할 메트릭
이름을 이 네이밍 규칙으로 함께 명시한다. "성공률을 측정한다"처럼 메트릭 이름 없는
SLI 서술은 L3 완결 기준(72번 §1)을 충족하지 못한 것으로 간주한다.

---

## 4. `event` 필드 어휘 — 상태 전이는 항상 과거분사

105번(동시성 표준)의 `ConcurrencyConflictError`처럼 실패도 1급 이벤트로 로깅한다.
이름 규칙:

- 성공한 상태 전이: `<aggregate>_<past_participle>` — `membership_granted`,
  `consent_revoked`, `strategy_package_published`.
- 거부/실패: `<aggregate>_<verb>_denied` 또는 `_rejected` — `deployment_denied`,
  `command_rejected`.
- 동시성 충돌(105번 §2): 항상 `<aggregate>_concurrency_conflict`로 통일 — 도메인마다
  다른 이름을 쓰면 §5의 공용 alert 규칙을 만들 수 없다.
- 정책/리스크 거부: `policy_decision_denied`, `risk_decision_denied`(payload에
  `reason_code`는 107번 계약의 표준 에러 taxonomy, 즉 72번 §4의 `POLICY_*`/`RISK_*`
  코드를 그대로 사용).

이벤트 이름과 audit event type(예: 73번의 `trust.membership_granted.v1`)은
**같은 어근을 공유**하되 audit event는 `<context>.<event>.v<N>` 형식(도메인
네임스페이스 + 계약 버전 포함), 로그의 `event` 필드는 네임스페이스 없이 짧게 —
로그는 사람이 grep하는 용도이고 audit event는 기계가 소비하는 계약이라는 차이를
반영한다.

---

## 5. 공용 alert 규칙 — 도메인 무관하게 항상 켜는 4가지

73번 §10처럼 각 도메인이 고유 alert(예: "freshness coverage decline")를 갖는 것과
별개로, §4의 공용 이벤트 어휘를 쓰는 한 아래 4개는 **모든 새 bounded context에
자동으로 적용 가능**하다(문서마다 재정의할 필요 없음, 인프라가 `component=
foundation.*`로 필터링해 일괄 등록):

1. `*_concurrency_conflict` 이벤트 비율이 5분 이동평균 대비 급증 — 105번 §1의
   재발 패턴을 조기 탐지(같은 결함이 또 어딘가에서 발생 중일 수 있음).
2. `outbox`/projection consumer의 최고령 미처리 이벤트 age >5분 (73번 §10과 동일
   임계값을 표준화 — 도메인별로 다른 숫자를 쓰려면 명시적 근거 필요).
3. `mode=live_blocked` 카운터가 0보다 큼 — PAPER 전용 경로에서 LIVE 시도가
   차단됐다는 뜻이며, 설정 오류 또는 공격 시도 둘 다 즉시 조사 대상(RT-02).
4. 같은 `trace_id`에 대해 서로 다른 `tenant_id`가 관측됨 — RT-01(confused
   deputy)의 실행 시점 탐지 신호.

---

## 6. 이 문서가 아직 못 정한 것

- 실제 로그 수집/저장 백엔드(35번 §2.6 Observability Service가 미구현) — 이 문서는
  필드 스키마만 고정하고, 어디로 보내는지는 71번 이후 인프라 작업 대상.
  `component`/`event` 네이밍이 먼저 고정돼 있어야 나중에 어떤 백엔드를 쓰든
  대시보드·alert 정의를 재작성하지 않는다는 게 이 문서 존재의 실익이다.
- 로그 보존 기간·PII 분류별 retention(73번 §8이 Trust 도메인에 한해 다룸) — 전
  도메인 공통 retention 정책은 27번(security/reliability governance) 후속 L3 대상.
