# 07. 로깅 포맷 및 Config 구조 — v1.3

> **v1.2(2026-08-10) = "실제 구현 가능성 검증" 라운드.** §7.2-A(suitability_scoring.yaml)
> 신설 — 16번 SuitabilityService.score()가 파일명만 참조하고 실제 구조가
> 없어 구현자가 막힐 지점이었음을 "이 문서만 갖고 실제 코딩이 가능한가"
> 시뮬레이션 중 발견.

> **v1.1(2026-08-10) = "0번부터 재검토" 라운드 — 번호 충돌 정정.** §7.1~7.3이
> 정책문서(docx) 7장(Exchange Adapter 관련, 7.4~7.10-A 등)과 번호가 겹치는
> 것을 발견 — 06/08/09번과 동일 라운드에 동일 조치.

> 근거: 8.10(Audit Log), 8.2-B(Risk 수치), 13.1(하드코딩 금지 원칙)

## §7.1 로깅 포맷 (구조화 JSON Lines)

```python
# src/core/logging/schema.py
class LogEntry(BaseModel):
    timestamp: datetime
    level: str  # DEBUG/INFO/WARNING/ERROR/CRITICAL
    module: str  # "exchanges.bitget.adapter" 등 dotted path
    event_type: str  # 05번 문서 Topic 명명규칙과 동일 체계 사용 (예: "order.status.changed")
    correlation_id: str | None  # task_id 또는 order_id — 하나의 요청 흐름을 관통 추적
    message: str
    extra: dict = Field(default_factory=dict)


# 로그 레벨 사용 기준
# DEBUG    — 개발 중에만. 프로덕션 기본 비활성.
# INFO     — 정상 주문 생성/체결, 정상 상태 전이.
# WARNING  — 재시도 발생, Reconciliation 1회 불일치(8.4), Circuit Breaker 경고 단계.
# ERROR    — Handler 예외(EventHandlerError), 주문 거부, API 인증 실패.
# CRITICAL — Watchdog 발동, Circuit Breaker 거래중지 이상, Kill Switch 발동.
#            이 레벨은 반드시 audit_log 테이블에도 동시 기록(8.10 원칙).
```

- `correlation_id`는 `AIOSTask.task_id`(4.3) 또는 `Order.client_order_id`(7.5)를 사용해, 하나의 요청이 Loader→Parser→...→Adapter를 관통하는 동안 로그로 추적 가능하게 한다.
- Phase 1은 stdout으로 JSON Lines 출력 → 로그 수집기(Datadog/Loki 등)는 팀 확정 후 연결. 지금 특정 도구를 미리 고정하지 않는다(과잉설계 방지, 17.9-A).

## §7.2 risk_policy.yaml 실제 구조

> 8.2-B의 8개 Draft 지표를 코드 하드코딩이 아닌 이 파일로 관리한다. 변경 시 반드시 Git 커밋 + CODEOWNERS 승인(FROZEN Zone과 동일한 보호 수준).

```yaml
# config/risk_policy.yaml
# 이 파일은 FROZEN Zone과 동일하게 취급 — 15.6-A CODEOWNERS 보호 대상
# 마지막 인간 승인: (미기재 — 실제 승인 시 ADR 번호와 승인자 기록)

version: "draft-1"

daily_loss:
  warning_pct: 3.0
  halt_pct: 5.0

max_drawdown:
  warning_pct: 10.0
  hard_stop_pct: 15.0

leverage:
  default_max: 3.0
  # 8.6-A-1 강화 — Reference 커버리지 등급별 조정 배수
  coverage_multiplier:
    high: 1.0
    medium: 0.7
    low: 0.5

position_concentration:
  single_asset_max_pct: 20.0

strategy_allocation:
  unverified_max_pct: 10.0
  certified_level4_max_pct: 25.0

var:
  confidence: 0.95
  horizon_days: 1
  max_pct: 5.0

correlation_risk:
  threshold: 0.7
  aggregate_exposure_max_pct: 30.0

trade_frequency:
  anomaly_multiplier: 3.0  # 직전 24시간 대비

# 8.6 Circuit Breaker 단계 임계치
circuit_breaker:
  warning:
    api_error_rate_pct: 10.0
    data_delay_sec: 2.0
  restricted:
    api_error_rate_pct: 25.0
    order_reject_rate_pct: 15.0
  halted:
    data_delay_sec: 5.0
  emergency:
    daily_loss_pct: 5.0
    api_disconnect_sec: 30.0

# 8.6-A Watchdog
watchdog:
  loss_threshold_pct: 7.0
  unresponsive_sec: 30
  window_min: 5

# 8.1-A Data Distrust Mode (히스테리시스)
data_distrust:
  enter_threshold_pct: 1.5
  exit_threshold_pct: 0.75
  exit_sustain_sec: 60
```

- 이 YAML의 모든 수치는 **여전히 Draft**다 — 8.2-B 원문과 동일하게, 실제 시장 데이터·백테스트 검증 및 인간 승인 없이 프로덕션에 적용하지 않는다.
- 로딩: `Loader.load_config(path)`(03번 문서)가 이 파일을 읽어 Pydantic 모델로 검증 후 반환. 코드 어디에도 이 수치를 하드코딩하지 않는다.

## §7.2-A `config/suitability_scoring.yaml` (신규 — "구현 가능성 검증" 라운드에서
발견: 16번 SuitabilityService.score()가 파일명만 참조하고 실제 구조가 어디에도
없어 구현자가 막힐 지점이었음)

```yaml
# FD-15.1 적합성평가 설문 점수화 — 전부 Draft, 18.3/19장 법률검토 후 확정
questions:
  investment_experience_years:
    weight: 0.2
    bands:  # 연차 구간별 배점(0~10)
      - {max: 1, score: 2}
      - {max: 3, score: 5}
      - {max: 10, score: 8}
      - {max: null, score: 10}  # null = 무제한 상한
  capital_at_risk_pct:
    weight: 0.25
    bands:
      - {max: 10, score: 3}
      - {max: 30, score: 6}
      - {max: 50, score: 8}
      - {max: null, score: 10}
  max_acceptable_loss_pct:
    weight: 0.3  # 가장 높은 가중치 — 손실감내수준이 위험등급의 핵심 지표
    bands:
      - {max: 10, score: 2}
      - {max: 20, score: 5}
      - {max: 40, score: 8}
      - {max: null, score: 10}
  investment_horizon:
    weight: 0.15
    scores: {SHORT_TERM: 8, LONG_TERM: 3}  # 단기지향일수록 고위험 성향으로 가정(Draft)
  liquidity_needs:
    weight: 0.1
    scores: {LOW: 8, MEDIUM: 5, HIGH: 2}

# 가중합산 점수(0~10) → 등급 매핑
grade_bands:
  - {max: 4.0, grade: "안정형"}
  - {max: 7.0, grade: "중립형"}
  - {max: null, grade: "공격형"}
```

- `SuitabilityService.score()`는 이 파일을 `Loader.load_config()`로 읽어
  각 문항 응답을 `bands`/`scores`에 대조해 0~10점 산출 후 `weight`로 가중
  합산, 최종 점수를 `grade_bands`로 등급 변환한다.
- **이 파일의 모든 구간·가중치·등급 3단계 구분은 Draft다** — FD-15장 헤더가
  이미 명시한 대로 18.3/19장 법률검토(표준 적합성평가 체계와의 정합성
  확인)가 완료되기 전까지 실제 서비스 등급 산정 근거로 쓰지 않는다.

## §7.3 Secrets 관리 (Phase 1)

```
config/
├── risk_policy.yaml      # 버전관리 대상, CODEOWNERS 보호
├── suitability_scoring.yaml  # 버전관리 대상, CODEOWNERS 보호(§7.2-A, v1.2 신설)
├── .env                  # 버전관리 제외(.gitignore), API Key/Secret
└── .env.example           # 버전관리 대상, 키 이름만(값은 비움)
```

- Phase 1은 `.env` 파일 + `Loader.load_env_secrets()`로 충분 — Vault·AWS Secrets Manager 등은 팀 규모·인프라 확정 후 검토(지금 도입은 과잉설계).
- `.env`는 절대 Git에 포함하지 않는다. `SecretBundle.__repr__`은 마스킹 처리(02번 문서 원칙 재확인).
- KIS의 `app_key`/`app_secret`, Bitget의 `api_key`/`api_secret` 모두 이 방식으로 통일.

### `.env.example` 실제 전체 목록 (v1.2 신설 — "구현 가능성 검증" 라운드에서
발견: FD-12.1이 "07번 §7.3 키 재사용"이라고 여러 차례 인용해온 암호화 키
자체가 이 문서 어디에도 실제로 정의된 적이 없었고, 11번 §11.x가 목표로
삼은 ".env.example 복사 후 API 키만 채우면 즉시 개발 가능"이 실제로는
불가능한 상태였음 — 이 문서만 보고 구현을 시작하는 사람이 무엇을 채워야
하는지 알 방법이 없었음)

```bash
# --- 애플리케이션 ---
APP_ENV=development                    # development | staging | production
LOG_LEVEL=INFO

# --- 데이터베이스 (ADR-2026-08-10-B: PostgreSQL + SQLAlchemy async + asyncpg) ---
DATABASE_URL=postgresql+asyncpg://user:password@localhost:5432/aios_dev

# --- 인증 (FD-11.1, ADR-2026-08-10-B: JWT) ---
JWT_SECRET_KEY=                        # openssl rand -hex 32 등으로 생성, 절대 공유 금지
JWT_ALGORITHM=HS256
JWT_EXPIRE_MINUTES=60

# --- 거래소 자격증명 암호화 (FD-12.1, AES-256-GCM) ---
CREDENTIAL_ENCRYPTION_KEY=             # 32바이트 키, openssl rand -hex 32
                                        # exchange_credentials.api_key/api_secret,
                                        # withdrawal_whitelist.destination_address(FD-11.5) 암호화에 재사용

# --- 거래소 API (FD-1.1 정정각주 — 로컬 개발/CI 테스트 전용 단일 계정,
#     이름은 FD-1.1 원문과 통일. 실제 프로덕션은 사용자별로 exchange_credentials
#     테이블에 암호화 저장(FD-12.1) — 이 4개와 혼동 금지) ---
BITGET_API_KEY=
BITGET_API_SECRET=
KIS_APP_KEY=
KIS_APP_SECRET=

# --- 알림 (FD-17.1) ---
SMTP_HOST=
SMTP_PORT=587
SMTP_USER=
SMTP_PASSWORD=
FCM_SERVER_KEY=                        # Android 푸시(FD-21.1), Draft — 착수 시 확정
APNS_KEY_ID=                           # iOS 푸시(FD-21.1), Draft — 착수 시 확정

# --- CORS (프론트엔드 로컬 개발) ---
CORS_ALLOWED_ORIGINS=http://localhost:5173
```
