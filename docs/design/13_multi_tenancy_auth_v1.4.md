# 13. 멀티테넌시 및 인증 (Multi-Tenancy & Auth) — 신규 근간 — v1.4

> **다자산군 확장(ADR-2026-08-28) 영향 검토 결과**: 변경 없음.
> `exchange_credentials.exchange`(§13.3)는 이미 자산군과 무관한 문자열이고,
> 자산군별 지원 여부는 02번 `ExchangeCapability.supported_asset_classes`가
> 담당한다 — 사용자·인증·자격증명 계층은 자산군을 몰라도 된다(관심사 분리
> 유지). 이 문서 자체는 개정하지 않음.
>
> **개정이력**: v1.0(기존) → v1.1(2026-08-10, users 테이블에 risk_profile 등
> FD-15 컬럼, strategy_purchases에 FD-13.7 중개수수료 컬럼, is_verifier/
> is_platform_admin 플래그 병합) → v1.2(2026-08-10, 재점검 라운드: "14번 문서
> §14.x" 표기로 마켓플레이스 상세 문서와의 참조 명확화) → **v1.3(2026-08-10)
> = 번호 충돌 정정.** 이 문서의 §13.1~13.8이 정책문서(docx) 13장(실무 운영
> 기준, 13.1 개발원칙~13.6 최종개발판단기준 — 특히 13.4는 정책문서에서
> "ADR 템플릿"인데 이 문서에선 "기존 테이블에 user_id 전파"로 완전히 다른
> 내용이었음)과 번호가 겹치는 것을 발견 — 15/16/17번과 동일 라운드에 동일
> 조치. 모든 최상위 헤더를 "§13.X"로 전면 변경. → **v1.4(2026-08-28,
> 작업트리 11.2 착수 중 발견)**: FD-11.1 예외상황("5회 연속 로그인 실패 시
> 15분 계정 잠금")이 실제 처리 단계에 명시돼 있는데, 정작 users DDL에
> 그 상태를 저장할 컬럼이 없었음(설계 누락) — `failed_login_attempts`,
> `locked_until` 컬럼 신설.
>
> 2026-08 스콥 확정: 초기 2~3인 → MVP 10인, 사용자간 P2P 마켓플레이스 필요, Human Approval은
> 플랫폼 비관여(사용자 자율설정). 이 세 결정이 00~12번 스펙 전반에 미치는 영향을 정리하고,
> 새로 필요한 근간(Users, Auth, 거래소 자격증명, Human Approval 2계층)을 여기서 확정한다.
>
> **이 문서는 04번(DB), 07번(Config), 정책문서 4.9(Human Approval)의 개정판 역할을 겸한다** —
> 기존 문서를 대체하지 않고, 변경된 부분만 여기서 명시하며 실제 착수 시 04/07번에 병합한다.

## §13.1 Human Approval 2계층 구조 (v3.1 정책문서 4.9 개정)

기존 정책문서 4.9~4.9-A는 "승인권자 2인"을 플랫폼 단일 주체로 전제했다. 실제 서비스 형태(다수 사용자, 각자 본인 자금)가 확정되면서 이를 2계층으로 분리한다.

| 계층 | 대상 | 승인권자 | 근거 |
|---|---|---|---|
| **플랫폼 레벨** | DevEngine 거버넌스, 시스템 전체 Kill Switch, 16.6 메타통제면 관련 결정 | 플랫폼 운영자 중 서로 다른 인간 2인(기존 4.9 원칙 그대로 유지 — **여전히 미해결 항목**, 20.1-B 참조) | 16.6, 4.9 |
| **사용자 레벨** | 개별 사용자의 Critical Risk(본인 자금 매매) | **사용자가 자율 설정** — 본인 1인 단독도 가능, 가족·공동투자자를 2인째로 세우는 것도 사용자 선택. 플랫폼은 이 설정에 관여하지 않는다. | 신규(v3.1) |

**중요한 경계**: 사용자 레벨 승인이 "본인 혼자"로 설정되어 있어도, 8.2-A Master Authority(결정론적 Risk가 LLM 위)와 8.6 Circuit Breaker/Watchdog(AI 이상행동 자체를 막는 장치)는 사용자 설정과 무관하게 항상 작동한다 — **Human Approval 자율화는 "인간의 판단을 안전장치로 쓸지 말지"의 선택이지, "AI를 무제한 신뢰한다"는 뜻이 아니다.** 즉 시스템이 이상행동을 하면 Watchdog이 막고, 정상 범위 내 결정을 사용자 승인 없이 진행할지는 사용자가 고른다.

```python
# src/data/models/approval_settings.py (신규)
class ApprovalMode(str, Enum):
    SOLO = "SOLO"              # 본인 1인 승인 (기본값)
    DUAL = "DUAL"               # 본인 + 지정한 제2인
    AUTO_WITHIN_LIMIT = "AUTO_WITHIN_LIMIT"  # 특정 한도 이내는 자동승인(Draft, 향후 검토)


class UserApprovalSettings(BaseModel):
    user_id: UUID
    mode: ApprovalMode = ApprovalMode.SOLO
    second_approver_contact: Optional[str] = None  # DUAL일 때만
    mandatory_wait_seconds: int = 60  # 4.9-A 강제대기 — 이 값 자체는 사용자가 줄일 수 없음(Draft 하한)
```

- `mandatory_wait_seconds`의 **하한(예: 60초)은 플랫폼이 강제**한다 — "즉시승인" 자체를 사용자가 끌 수 없게 해서 반사적 승인(4.9-A가 막으려던 것)을 계속 방지한다. 자율화는 "누가 승인하는가"에 대한 것이지 "즉시 승인 가능 여부"에 대한 것이 아니다.

## §13.2 users 테이블 및 인증 (신규)

```sql
-- ============================================================
-- Users (신규)
-- ============================================================
CREATE TABLE users (
    user_id         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    email           VARCHAR(255) NOT NULL UNIQUE,
    password_hash   VARCHAR(255) NOT NULL,  -- Argon2id 권장(Draft)
    display_name    VARCHAR(100),
    mfa_enabled     BOOLEAN NOT NULL DEFAULT FALSE,
    mfa_secret      VARCHAR(255),  -- TOTP secret, 암호화 저장
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_login_at   TIMESTAMPTZ,
    status          VARCHAR(20) NOT NULL DEFAULT 'ACTIVE'
        CHECK (status IN ('ACTIVE','SUSPENDED','PENDING_DELETION','DELETED')),
        -- v1.1: PENDING_DELETION 추가 (FD-11.4 회원탈퇴, 유예기간 처리)
    deletion_requested_at TIMESTAMPTZ,  -- v1.1: FD-11.4
    risk_profile    VARCHAR(20)
        CHECK (risk_profile IN ('안정형','중립형','공격형')),
        -- v1.1: FD-15.2, Draft 등급체계 — 법률검토 후 변경 가능
    risk_profile_assessed_at TIMESTAMPTZ,  -- v1.1: FD-15.2
    is_verifier         BOOLEAN NOT NULL DEFAULT FALSE,  -- v1.2: FD-18, 15.6에서 예고, 이번에 병합
    is_platform_admin   BOOLEAN NOT NULL DEFAULT FALSE,  -- v1.2: FD-18, 15.6에서 예고, 이번에 병합
    failed_login_attempts SMALLINT NOT NULL DEFAULT 0,   -- v1.4: FD-11.1 로그인 실패 잠금
    locked_until        TIMESTAMPTZ                      -- v1.4: FD-11.1, 5회 실패 시 15분 잠금
);

-- v1.1 신설: 위험등급 재평가 이력 보존 (FD-15.2, 4.6-A Memory 버전관리 원칙과 동일 정신)
CREATE TABLE risk_profile_history (
    id                  BIGSERIAL PRIMARY KEY,
    user_id             UUID NOT NULL REFERENCES users(user_id),
    risk_profile        VARCHAR(20) NOT NULL,
    assessment_answers  JSONB NOT NULL,   -- FD-15.1 설문 응답 원본 보존
    assessed_at         TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_risk_profile_history_user ON risk_profile_history(user_id);

CREATE TABLE user_approval_settings (
    user_id                  UUID PRIMARY KEY REFERENCES users(user_id),
    mode                     VARCHAR(20) NOT NULL DEFAULT 'SOLO',
    second_approver_contact  VARCHAR(255),
    mandatory_wait_seconds   INT NOT NULL DEFAULT 60 CHECK (mandatory_wait_seconds >= 60),
    updated_at               TIMESTAMPTZ NOT NULL DEFAULT now()
);
```

- 비밀번호는 Argon2id 등 적정 해시(Draft — 실제 착수 시 라이브러리 확정), **MFA는 Phase 1부터 필수 검토 대상**(4.9의 MFA 원칙을 플랫폼 사용자에게도 적용하는 것이 정합성상 맞다 — 최종 확정은 착수 시).
- `status`로 논리 삭제(soft delete) — 사용자 삭제 시에도 `orders`/`audit_log`(회계·감사 근거) 보존 필요.

## §13.3 거래소 자격증명 — 사용자별 암호화 저장 (기존 `.env` 단일세트 → 대체)

```sql
CREATE TABLE exchange_credentials (
    id              BIGSERIAL PRIMARY KEY,
    user_id         UUID NOT NULL REFERENCES users(user_id),
    exchange        VARCHAR(30) NOT NULL,
    api_key_encrypted     BYTEA NOT NULL,
    api_secret_encrypted  BYTEA NOT NULL,
    extra_encrypted       BYTEA,
    is_active       BOOLEAN NOT NULL DEFAULT TRUE,
    linked_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    revoked_at      TIMESTAMPTZ,
    UNIQUE (user_id, exchange)
);
```

- **암호화 키 관리**: Phase 1은 애플리케이션 레벨 암호화 키를 `.env`(07번 §7.3 원칙 재사용, 단 이제 API키가 아니라 "암호화 키" 하나만 담음)로 관리 — Vault 등은 사용자 수 증가 시 재검토(과잉설계 방지 원칙 유지).
- FD-3.1(인증)은 이제 "시스템 전역 1세트"가 아니라 **"이 Task를 요청한 user_id의 자격증명을 조회 후 인증"**으로 갱신된다 — 03/02번 Adapter 초기화 시그니처에 `user_id` 파라미터 추가 필요(착수 시 반영).

## §13.4 기존 테이블에 `user_id` 전파 (04번 개정)

| 테이블 | user_id 필요 여부 | 비고 |
|---|---|---|
| `tasks` | nullable | DevEngine 자체 작업은 NULL, 사용자 대행 작업(전략생성 등)은 값 존재 |
| `strategies` | `owner_user_id` | 이 전략을 만든 사용자. 마켓플레이스 구매자는 별도 테이블(13.5) |
| `orders` | NOT NULL | 반드시 특정 사용자 소유 |
| `positions` | NOT NULL | 상동 |
| `reconciliation_events` | NOT NULL | 사용자별 계좌 불일치이므로 |
| `audit_log` | nullable | 시스템 레벨 행위는 NULL, 사용자 행위는 값 존재 |
| `memory_entries` | 보류(Draft) | Phase 1은 시스템 전역 Memory 유지 — 사용자별 격리는 Phase 3+ 재검토(사용자 10명 개별 Memory는 학습신호가 너무 희소) |
| `capability_tokens` | 변경 없음 | 16.2는 DevEngine 자체 작업 인증 — 최종 사용자 인증과 별개 개념 |

- 이 전파는 04번 DDL에 `ALTER TABLE ... ADD COLUMN user_id ...` 형태로 실제 착수 시 반영한다.
- **RLS(Row-Level Security) 검토**: PostgreSQL Row-Level Security로 "이 커넥션은 자기 user_id 행만 볼 수 있다"를 DB 레벨에서 강제하는 방안을 Draft로 남긴다 — 애플리케이션 버그로 인한 사용자간 데이터 노출을 DB 레벨에서 이중 방어(15.6-A "규칙이 아니라 물리적 차단" 철학과 동일선상).

## §13.5 마켓플레이스 최소 스키마 (P2P 골격만 — 상세는 별도 문서 예정)

> 리스팅 가격정책·평판시스템·9.5-A Certified Badge 검증 플로우 등 상세 설계는 스콥이 커서 별도 문서(14번, 다음 라운드)로 분리한다. 여기서는 다른 테이블과의 관계만 고정한다.

```sql
CREATE TABLE strategy_listings (
    id                  BIGSERIAL PRIMARY KEY,
    strategy_id         VARCHAR(100) NOT NULL,
    strategy_version    VARCHAR(20) NOT NULL,
    seller_user_id       UUID NOT NULL REFERENCES users(user_id),
    price               NUMERIC(20,2),
    status              VARCHAR(20) NOT NULL DEFAULT 'DRAFT'
        CHECK (status IN ('DRAFT','PENDING_VERIFICATION','LISTED','DELISTED')),
    -- 'LISTED' 전환은 정책문서 9.5-A(Black/Killer Team 검증) 통과가 전제 — 상세 로직은 14번에서
    FOREIGN KEY (strategy_id, strategy_version) REFERENCES strategies(strategy_id, version),
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE strategy_purchases (
    id                          BIGSERIAL PRIMARY KEY,
    listing_id                  BIGINT NOT NULL REFERENCES strategy_listings(id),
    buyer_user_id                UUID NOT NULL REFERENCES users(user_id),
    purchased_at                 TIMESTAMPTZ NOT NULL DEFAULT now(),
    price_paid                   NUMERIC(20,2),
    payment_status                VARCHAR(20) NOT NULL DEFAULT 'PENDING_PAYMENT'
        CHECK (payment_status IN ('PENDING_PAYMENT','CONFIRMED')),
        -- v1.1 병합: 14번 문서 §14.1.4에서 이미 예고됐으나 미병합 상태였던 컬럼 —
        -- Phase 1 수동 정산 2단계 흐름(운영자 수동확인 후 CONFIRMED)
    platform_commission_rate     NUMERIC(5,4),   -- v1.1: FD-13.7, Draft 0.10~0.20
    platform_commission_amount   NUMERIC(20,2),  -- v1.1: FD-13.7
    seller_payout_amount         NUMERIC(20,2)   -- v1.1: FD-13.7, = price_paid - commission
    -- 결제 처리 자체(PG 연동 등)는 스콥 밖 — 14번에서 다룸
);

-- v1.1 병합: 14번 문서 §14.5.3에서 이미 예고됐으나 미병합 상태였던 컬럼 — 반복 분쟁 판매자 제재
ALTER TABLE users ADD COLUMN seller_suspended BOOLEAN NOT NULL DEFAULT FALSE;
```

## §13.6 기능설계 확장 (12번 문서에 추가될 FD 목록 — 다음 라운드에서 상세화)

```
FD-11 사용자 인증 및 계정관리
├── FD-11.1 회원가입/로그인
├── FD-11.2 MFA 설정
└── FD-11.3 승인 설정(ApprovalMode) 관리

FD-12 사용자별 거래소 연동
├── FD-12.1 거래소 자격증명 등록/해지
└── FD-12.2 자격증명 조회 및 Adapter 인증 연동(FD-3.1 개정)

FD-13 마켓플레이스 (스콥 큼 — 14번 문서에서 상세)
├── FD-13.1 전략 리스팅
├── FD-13.2 전략 검증(9.5-A 연동)
├── FD-13.3 전략 구매
└── FD-13.4 구매한 전략 실행 연동
```

**v1.1~v1.3 갱신(2026-08-10)**: 위 FD-11~13는 기능설계문서 v1.3에서 실제
FD-11.1~13.4 상세로 채워졌고, 비전 대조 갭리뷰로 FD-14(전략편집기)~FD-21(모바일 앱)
까지 순차 신설됐다 — 아래 13.7 표는 이 시점 기준 완료 상태로 갱신한다.

## §13.7 기존 스펙에 대한 영향 요약

| 기존 문서 | 필요 조치 | 상태(2026-08-10) |
|---|---|---|
| 00번 | 문서 목록에 13번 추가, 팀 구성 서술에 "사용자 2~3인→10인 멀티테넌시" 반영 | 미착수 — 00번 원문 편집은 실제 착수 시 반영 |
| 02번(Adapter) | `__init__`에 `user_id` 추가, 인증을 13.3 자격증명 테이블 조회로 변경 | 설계 완료(FD-12.2), 코드 착수 전 |
| 04번(DB) | 13.2~13.5 테이블 병합, 기존 테이블에 `user_id` 컬럼 추가 | **완료** — 04번/13번 양쪽에 v1.3까지 반영됨 |
| 06번(MVP 스콥) | "심볼 5개, 거래소 2개"는 시스템 전체 화이트리스트로 유지, 사용자 수(2~3→10)와 멀티테넌시 전제를 Definition of Done에 반영 | 미착수 — 06번 원문 편집은 실제 착수 시 반영 |
| 10번(작업트리) | 신규 대분류 "11. 인증·계정" "12. 거래소 연동(사용자별)" "13. 마켓플레이스 골격" 추가 필요 | **완료** — 대분류 11~21까지 10번 문서에 병합됨 |
| 12번(기능설계) | FD-11~13 상세화 필요 | **완료** — FD-11~21까지 기능설계문서 v1.3에 상세 명세됨 |
| 정책문서 4.9 | 본 문서 §13.1의 2계층 구조로 개정 필요(v3.2 예정) | v3.2에서 반영 확인(정책문서 4.9 본문에 2계층 구조 명시됨), 플랫폼 레벨 승인권자 실제 인선은 ADR-2026-08-10로 1인 체제 조건부 확정 |
| 15번(API 스펙) | (v1.1 시점엔 미존재) | **완료** — FD-11~21 전체 REST API·RBAC 정의됨(15번 문서) |

- 남은 "미착수" 두 항목(00번/06번 원문 자체 편집)은 문서 내용 자체는 이미 다른
  곳(13번, 기능설계문서)에 정확히 반영돼 있고, 00번/06번 원문에 그 사실을
  각주로 옮겨 적는 편집 작업만 남은 상태 — 21.1 Repository 분석 단계에서
  실제 코드 착수와 함께 정리해도 무방(내용 손실 없음, 순수 문서 정리 작업).

## §13.8 의도적으로 지금 안 정하는 것

- 결제 시스템(PG 연동) — 마켓플레이스 매출 처리는 실제 사업자 등록·정산 방식 확정 후(18장 실무 성공요인과 연동).
- 사용자별 Memory 격리 — §13.4에서 이미 보류 이유 명시.
- RLS 실제 적용 여부 — 아이디어만 Draft로 남기고, 팀 DB 운영 역량 확인 후 결정.
- FD-13(마켓플레이스) 상세 — 스콥이 이 문서 하나로 감당 안 됨, 명시적으로 다음 라운드로 분리.
