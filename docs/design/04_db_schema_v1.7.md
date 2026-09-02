# 04. DB 스키마 (PostgreSQL DDL, Draft) — v1.7

> **v1.7(2026-08-28) = 다자산군(Multi-Asset-Class) 확장 라운드.** ADR-2026-08-28
> 반영 — `orders`/`positions`에 `asset_class`/`option_type`/`strike_price`/
> `expiry_date`/`contract_multiplier`/`underlying_symbol` 컬럼 추가(전부
> nullable — 크립토/현물 행은 기존과 동일하게 NULL 유지, 파생상품 행에서만
> 채워짐). 01번 §1.0 `AssetClass`/`OptionType`과 1:1 대응.

> **v1.6(2026-08-10) = "구현자 리뷰 대조" 라운드.** 사용자가 제기한 리뷰
> 지적사항을 project 원본과 직접 대조 — `positions` 테이블에 `realized_pnl`/
> `unrealized_pnl`/`margin` 컬럼이 처음부터 없었음(01번 Pydantic `Position`
> 모델엔 있었는데 DDL엔 빠짐, 20라운드 넘는 이전 재점검에서도 계속 놓쳤던
> 실제 결함). 추가 완료.

> **v1.5(2026-08-10) = "클로드 코드 구현가능성" 검증 라운드.** `system_safety_state`
> 신설 — `SafetyLayerStatusProvider.is_blocked()`가 실제로 조회할 시스템
> 전역 Circuit Breaker 레벨을 저장할 곳이 어디에도 없었음(개별 실행 차단은
> 이미 strategy_executions.paused_by로 충분했으나, 시스템 전역 halted/
> emergency 상태는 여러 FastAPI 워커가 공유할 DB 영속화가 필요).

> 근거: 정책문서 4.3(Task 스키마), 9.11(FSM Strategy 스키마), 4.6-A(Memory), 8.10(Audit Log)
> 상태: DRAFT — 팀 확정 후 Alembic 등 마이그레이션 도구로 버전 관리 권장
> **개정이력**: v1.0(기존) → v1.1(2026-08-10, FD-14~21 신규 테이블: strategy_executions/
> notifications/notification_preferences/device_tokens, strategies.created_via 추가)
> → v1.2(2026-08-10, 재점검 라운드: strategy_executions.paused_by 컬럼 추가,
> device_tokens UNIQUE 제약을 부분 유니크 인덱스로 정정 — 재등록 시나리오 지원)
> → **v1.3(2026-08-10) = "0번부터 재검토" 라운드.** 헤더 "근거" 줄과 §4.1
> 섹션에서 정책문서 조항(4.3/9.11/4.6-A/8.10/13.4/16.3) 인용에 "정책문서"
> 접두어 명시, 자체 헤더도 "§4.1"로 변경 — 15/16/17/13번과 동일 라운드에
> 동일 조치(정책문서 4장 자체가 4.1~4.10까지 매우 넓어 잠재 위험이 있었음,
> 다만 이 문서 자신은 섹션이 §4.1 하나뿐이라 실제 충돌 표면은 작았음).
> 원칙(20.1-B §D): 8.2-B Risk 수치는 이 스키마의 데이터가 아니라 별도 `risk_policy.yaml`로 관리(하드코딩 금지)
> **v3.1 갱신 — 멀티테넌시 반영(13번 §13.4)**: 아래 테이블에 `user_id` 컬럼을 추가했다. `users`/`exchange_credentials`/`strategy_listings`/`strategy_purchases` 테이블 정의는 13번 문서 §13.2~13.5 참조(여기 중복 기재하지 않음).
> **v1.1 갱신(2026-08-10)** — 비전 대조 갭리뷰로 `strategy_executions`(FD-16)·
> `notifications`/`notification_preferences`(FD-17) 테이블 신설, `strategies`에
> `created_via` 컬럼 추가. `users`/`strategy_purchases`의 v1.1 변경분은 13번 문서에
> 병합됨(문서 하단 참조).

```sql
-- ============================================================
-- Tasks (4.3 AIOSTask)
-- ============================================================
CREATE TABLE tasks (
    task_id         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    parent_task_id  UUID REFERENCES tasks(task_id),
    user_id         UUID REFERENCES users(user_id),  -- v3.1: nullable — DevEngine 자체 작업은 NULL(13번 §13.4)
    objective       TEXT NOT NULL,
    assigned_agent  VARCHAR(100) NOT NULL,
    required_permission_level SMALLINT NOT NULL CHECK (required_permission_level BETWEEN 0 AND 6),
    status          VARCHAR(20) NOT NULL DEFAULT 'PENDING',
    input_payload   JSONB NOT NULL DEFAULT '{}',
    output_result   JSONB,
    retry_count     INT NOT NULL DEFAULT 0,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    completed_at    TIMESTAMPTZ,

    -- 16.2 Capability Token 연동
    capability_token_id UUID
);
CREATE INDEX idx_tasks_status ON tasks(status);
CREATE INDEX idx_tasks_assigned_agent ON tasks(assigned_agent);


-- ============================================================
-- Strategies (9.11 FSMStrategyConfig + 생애주기)
-- ============================================================
CREATE TABLE strategies (
    strategy_id     VARCHAR(100) NOT NULL,
    version         VARCHAR(20) NOT NULL,
    owner_user_id   UUID NOT NULL REFERENCES users(user_id),  -- v3.1: 이 전략을 만든 사용자(13번 §13.4)
    target_asset    VARCHAR(50) NOT NULL,
    market          VARCHAR(30) NOT NULL,
    exchange        VARCHAR(30) NOT NULL,
    fsm_definition  JSONB NOT NULL,          -- states/transitions 전체
    author_agent    VARCHAR(100) NOT NULL,
    lifecycle_status VARCHAR(30) NOT NULL DEFAULT 'IDEA'
        CHECK (lifecycle_status IN (
            'IDEA','RESEARCH','GENERATED','BACKTESTING','VALIDATING','STRESS_TESTING',
            'RISK_REVIEW','PAPER_TRADING','APPROVED','DEPLOYED','MONITORING','REVIEW',
            'RETIRED','REJECTED','FAILED'
        )),  -- v3.1: CHECK 제약 추가(09번 §9.1 #10)
    certified_badge BOOLEAN NOT NULL DEFAULT FALSE,   -- 9.5-A
    last_recertified_at TIMESTAMPTZ,                  -- 9.5-A Continuous Certification
    created_via     VARCHAR(20) NOT NULL DEFAULT 'EDITOR'
        CHECK (created_via IN ('EDITOR','AI_GENERATED','EVOLUTIONARY')),
        -- v1.1: FD-14 — 9.2 전략 생성 3가지 방식(Human/AI/Evolutionary)을 실제 데이터로 추적
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),

    PRIMARY KEY (strategy_id, version)
);

-- 4.6-A Memory-Strategy 출처 연결 (다대다)
CREATE TABLE strategy_memory_refs (
    strategy_id     VARCHAR(100) NOT NULL,
    strategy_version VARCHAR(20) NOT NULL,
    memory_id       UUID NOT NULL REFERENCES memory_entries(memory_id),
    FOREIGN KEY (strategy_id, strategy_version) REFERENCES strategies(strategy_id, version),
    PRIMARY KEY (strategy_id, strategy_version, memory_id)
);


-- ============================================================
-- Orders (8.3 Order State Machine)
-- ============================================================
CREATE TABLE orders (
    order_id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id              UUID NOT NULL REFERENCES users(user_id),  -- v3.1: 필수(13번 §13.4)
    client_order_id      VARCHAR(100) NOT NULL UNIQUE,  -- 7.5 멱등성 키
    exchange_order_id    VARCHAR(100),
    strategy_id          VARCHAR(100) NOT NULL,
    strategy_version     VARCHAR(20) NOT NULL,
    symbol               VARCHAR(30) NOT NULL,
    exchange             VARCHAR(30) NOT NULL,
    side                 VARCHAR(4) NOT NULL CHECK (side IN ('BUY','SELL')),
    order_type           VARCHAR(10) NOT NULL,
    quantity             NUMERIC(30,10) NOT NULL,
    price                NUMERIC(30,10),
    status               VARCHAR(20) NOT NULL DEFAULT 'CREATED',
    filled_quantity       NUMERIC(30,10) NOT NULL DEFAULT 0,
    average_fill_price    NUMERIC(30,10),
    is_liquidation        BOOLEAN NOT NULL DEFAULT FALSE,  -- 8.6-A-2 청산 주문 표시
    created_at            TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at            TIMESTAMPTZ NOT NULL DEFAULT now(),

    -- v1.7(ADR-2026-08-28) 다자산군 확장 — 크립토/현물 주문은 전부 NULL 유지.
    asset_class           VARCHAR(20),  -- 01번 AssetClass. 신규 주문은 착수 시 NOT NULL로 강화 검토
    option_type           VARCHAR(4) CHECK (option_type IN ('CALL','PUT')),
    strike_price          NUMERIC(30,10),
    expiry_date           TIMESTAMPTZ,
    contract_multiplier   NUMERIC(20,4),
    underlying_symbol     VARCHAR(30)
);
CREATE INDEX idx_orders_status ON orders(status);
CREATE INDEX idx_orders_strategy ON orders(strategy_id, strategy_version);
-- UNKNOWN 상태 재조회 대상 조회용
CREATE INDEX idx_orders_unknown ON orders(status) WHERE status = 'UNKNOWN';
-- v1.7: 자산군별 조회(예: 옵션 만기 임박 포지션 스캔) 대비
CREATE INDEX idx_orders_asset_class ON orders(asset_class) WHERE asset_class IS NOT NULL;


-- ============================================================
-- Positions
-- ============================================================
CREATE TABLE positions (
    id                   BIGSERIAL PRIMARY KEY,
    user_id              UUID NOT NULL REFERENCES users(user_id),  -- v3.1: 필수(13번 §13.4)
    symbol               VARCHAR(30) NOT NULL,
    exchange             VARCHAR(30) NOT NULL,
    strategy_id          VARCHAR(100) NOT NULL,
    quantity             NUMERIC(30,10) NOT NULL,
    average_entry_price  NUMERIC(30,10) NOT NULL,
    unrealized_pnl       NUMERIC(30,10) NOT NULL DEFAULT 0,  -- v1.6 추가 — "구현자 리뷰 대조" 라운드에서 발견
    realized_pnl         NUMERIC(30,10) NOT NULL DEFAULT 0,  -- v1.6 추가 — 01번 Pydantic Position 모델엔
                                                               -- unrealized_pnl/realized_pnl 둘 다 있었는데
                                                               -- 이 DDL엔 처음부터 둘 다 빠져 있었음(20라운드
                                                               -- 넘는 재점검에서도 계속 놓쳤던 실제 결함).
                                                               -- closed_at 시점에 realized_pnl을 확정 기록,
                                                               -- 이후 재진입해도 이 값은 불변(8.10 감사 원칙).
    leverage             NUMERIC(10,2) NOT NULL DEFAULT 1,
    margin               NUMERIC(30,10),  -- v1.6 추가 — 01번 Position.margin과 동일 근거로 함께 발견
    entry_time           TIMESTAMPTZ NOT NULL,
    closed_at            TIMESTAMPTZ,  -- v3.1: quantity=0 도달 시각. 행은 삭제하지 않고 유지(감사 추적, 8.10)
    updated_at           TIMESTAMPTZ NOT NULL DEFAULT now(),

    -- v1.7(ADR-2026-08-28) 다자산군 확장 — orders와 동일 원칙(파생 포지션만 채워짐).
    asset_class          VARCHAR(20),
    option_type          VARCHAR(4) CHECK (option_type IN ('CALL','PUT')),
    strike_price         NUMERIC(30,10),
    expiry_date          TIMESTAMPTZ,
    contract_multiplier  NUMERIC(20,4),
    underlying_symbol    VARCHAR(30),

    UNIQUE (symbol, exchange, strategy_id, entry_time)  -- v3.1: entry_time 추가 — 청산 후 재진입 시 새 행 허용
);
-- v3.1 원칙(09번 §9.1 #9): 포지션 청산(quantity=0) 시 행을 삭제하지 않는다.
-- closed_at을 기록하고 유지 — Audit·Performance Memory(4.6) 분석에 과거 포지션 이력이 필요하다.
-- '현재 보유 중인 포지션'을 조회할 때는 애플리케이션 레벨에서 quantity<>0 AND closed_at IS NULL로 필터링한다.


-- ============================================================
-- Memory Entries (4.6-A)
-- ============================================================
CREATE TABLE memory_entries (
    memory_id       UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    memory_type     VARCHAR(20) NOT NULL,  -- DECISION/FAILURE/PERFORMANCE 등
    content         JSONB NOT NULL,
    source_agent    VARCHAR(100) NOT NULL,
    source_task_id  UUID REFERENCES tasks(task_id),
    confidence      REAL NOT NULL DEFAULT 0.5 CHECK (confidence BETWEEN 0 AND 1),
    status          VARCHAR(20) NOT NULL DEFAULT 'UNVERIFIED',  -- ProvenanceStatus
    verified_by     VARCHAR(100),
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    verified_at     TIMESTAMPTZ
);
CREATE INDEX idx_memory_status ON memory_entries(status);
CREATE INDEX idx_memory_type ON memory_entries(memory_type);


-- ============================================================
-- Audit Log (8.10 — Append-only, 16.3 DevEngine 발 로그도 동일 체계 사용)
-- ============================================================
CREATE TABLE audit_log (
    log_id          BIGSERIAL PRIMARY KEY,
    user_id         UUID REFERENCES users(user_id),  -- v3.1: nullable — 시스템 레벨 행위는 NULL(13번 §13.4)
    actor_agent     VARCHAR(100) NOT NULL,
    action_type     VARCHAR(50) NOT NULL,
    target_type     VARCHAR(50),          -- 'order' | 'strategy' | 'task' | 'policy' 등
    target_id       VARCHAR(100),
    decision_data   JSONB NOT NULL,       -- 판단 근거·데이터·검증결과 전체
    verification_chain JSONB,             -- 4.5 Verification Chain 통과 이력
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
-- WORM(Write-Once-Read-Many) 강제 — 16.3 원칙
REVOKE UPDATE, DELETE ON audit_log FROM PUBLIC;
CREATE INDEX idx_audit_actor ON audit_log(actor_agent);
CREATE INDEX idx_audit_target ON audit_log(target_type, target_id);
CREATE INDEX idx_audit_created ON audit_log(created_at);


-- ============================================================
-- Reconciliation Log (8.4)
-- ============================================================
CREATE TABLE reconciliation_events (
    id              BIGSERIAL PRIMARY KEY,
    user_id         UUID NOT NULL REFERENCES users(user_id),  -- v3.1: 필수(13번 §13.4)
    symbol          VARCHAR(30) NOT NULL,
    exchange        VARCHAR(30) NOT NULL,
    order_id        UUID REFERENCES orders(order_id),   -- v3.1 추가: 특정 주문이 원인인 경우 연결
    position_id     BIGINT REFERENCES positions(id),    -- v3.1 추가: 특정 포지션이 원인인 경우 연결
    internal_value  JSONB NOT NULL,   -- 내부 상태(포지션 등)
    external_value  JSONB NOT NULL,   -- 거래소 실제 상태
    discrepancy_pct NUMERIC(10,4),
    resolved        BOOLEAN NOT NULL DEFAULT FALSE,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
-- 8.4 에스컬레이션 기준(1시간 내 3회, 24시간 내 5회) 계산용 인덱스
CREATE INDEX idx_reconciliation_symbol_time ON reconciliation_events(symbol, exchange, created_at);
CREATE INDEX idx_reconciliation_order ON reconciliation_events(order_id);


-- ============================================================
-- Capability Tokens (16.2 — v3.1 신설, 09번 §9.1 #1 반영)
-- ============================================================
CREATE TABLE capability_tokens (
    token_id        UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    task_id         UUID NOT NULL REFERENCES tasks(task_id),
    repository      VARCHAR(100) NOT NULL,
    branch          VARCHAR(100) NOT NULL,
    allowed_paths   JSONB NOT NULL,   -- 발급 시 15.6-A Zone 분류와 대조 검증된 경로 목록
    operations      JSONB NOT NULL,   -- ["read","write"] 등
    network_access  BOOLEAN NOT NULL DEFAULT FALSE,
    secrets_scope   JSONB NOT NULL DEFAULT '[]',  -- 원칙상 공집합이 기본값(16.2)
    issued_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    ttl_seconds     INT NOT NULL,
    revoked_at      TIMESTAMPTZ,      -- 완료/취소 시 즉시 무효화(16.2)
    revoked_reason  VARCHAR(50)       -- 'task_completed' | 'ttl_expired' | 'cancelled'
);
CREATE INDEX idx_cap_tokens_task ON capability_tokens(task_id);
-- tasks.capability_token_id의 실제 FK 제약(순환참조 방지 위해 별도 ALTER로 추가)
ALTER TABLE tasks ADD CONSTRAINT fk_tasks_capability_token
    FOREIGN KEY (capability_token_id) REFERENCES capability_tokens(token_id);
```

## v1.1~v1.3 신규 테이블 (2026-08-10, 비전 대조 갭리뷰)

```sql
-- ============================================================
-- System Safety State (FD-9.4/9.4b — "클로드 코드 구현가능성" 라운드 신설)
-- SafetyLayerStatusProvider가 개별 실행 차단 여부는 strategy_executions.
-- paused_by로 이미 조회 가능(FD-16.3)하지만, 시스템 "전역" Circuit Breaker
-- 레벨(API 오류율 등 시스템 전체 지표 기반, 특정 execution_id에 종속되지
-- 않음)을 저장할 곳이 어디에도 없었음 — 여러 FastAPI 워커 프로세스가
-- in-memory 변수만으로는 이 상태를 공유할 수 없어 DB 영속화 필요.
-- ============================================================
CREATE TABLE system_safety_state (
    id                  SMALLINT PRIMARY KEY DEFAULT 1 CHECK (id = 1),  -- 단일 행만 허용
    circuit_breaker_level VARCHAR(20) NOT NULL DEFAULT 'normal'
        CHECK (circuit_breaker_level IN ('normal','warning','restricted','halted','emergency')),
    reactivation_approval_id BIGINT,  -- FD-9.4b 재가동 승인 대기 중이면 approval_requests.id 참조(Draft, 승인테이블 미정의 — 착수 시 FD-10.1 승인테이블과 통합)
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);
INSERT INTO system_safety_state (id) VALUES (1) ON CONFLICT DO NOTHING;
-- FD-9.4: 단일 행을 SELECT ... FOR UPDATE로 잠그고 갱신 — 동시성 제어 필요(Draft)


-- ============================================================
-- Withdrawal Whitelist (FD-11.5 — "다시 0번부터" 라운드 신설)
-- 정책문서 7.10-A/20.1-B가 "평상시 미리 등록"을 요구했으나, 이 화이트리스트를
-- 채우는 기능(FD) 자체가 없어 FD-10.3(패닉 프롬프트)이 참조할 데이터가
-- 실제로는 만들어질 방법이 없었음.
-- ============================================================
CREATE TABLE withdrawal_whitelist (
    id                  BIGSERIAL PRIMARY KEY,
    user_id             UUID NOT NULL REFERENCES users(user_id),
    exchange            VARCHAR(30) NOT NULL,
    destination_address TEXT NOT NULL,  -- 암호화 저장(07번 §7.3 CREDENTIAL_ENCRYPTION_KEY 재사용, v1.4 확정 — "구현 가능성 검증" 라운드)
    label               VARCHAR(100),
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now()
    -- 삭제(revoke)는 의도적으로 미제공 — 화이트리스트에서 빼는 것도 위기
    -- 상황 중에는 공격 표면이 될 수 있어, 착수 시 동일하게 "평상시만
    -- 삭제 가능" 원칙 검토 필요(FD-11.5 예외상황과 동일 정신)
);
CREATE INDEX idx_withdrawal_whitelist_user ON withdrawal_whitelist(user_id);


-- ============================================================
-- Reviews / Disputes (FD-13.9/13.10 — "0번부터 재검토" 라운드 신설)
-- 14번 문서(마켓플레이스 상세) §14.2/§14.5가 처음부터 요구했으나 5개 라운드
-- 동안 반영이 누락됐던 테이블 — 17번 프론트엔드는 이미 화면(WriteReviewPage,
-- DisputeSubmitPage)까지 만들어져 있었는데 정작 저장소가 없었음.
-- ============================================================
CREATE TABLE reviews (
    id                  BIGSERIAL PRIMARY KEY,
    listing_id          BIGINT NOT NULL REFERENCES strategy_listings(id),
    reviewer_user_id    UUID NOT NULL REFERENCES users(user_id),
    rating              SMALLINT NOT NULL CHECK (rating BETWEEN 1 AND 5),
    comment             TEXT,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (listing_id, reviewer_user_id)  -- FD-13.9: 1구매 1리뷰 원칙
);
CREATE INDEX idx_reviews_listing ON reviews(listing_id);

CREATE TABLE disputes (
    id                  BIGSERIAL PRIMARY KEY,
    purchase_id         BIGINT NOT NULL REFERENCES strategy_purchases(id),
    submitted_by        UUID NOT NULL REFERENCES users(user_id),
    reason              TEXT NOT NULL,
    status              VARCHAR(20) NOT NULL DEFAULT 'OPEN'
        CHECK (status IN ('OPEN', 'RESOLVED')),
    resolution_decision VARCHAR(30)
        CHECK (resolution_decision IN ('NORMAL_RISK_REALIZATION', 'DELISTED_AND_REFUND')),
    resolution_reason   TEXT,
    resolved_by         UUID REFERENCES users(user_id),
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    resolved_at         TIMESTAMPTZ
);
CREATE INDEX idx_disputes_status ON disputes(status);
CREATE UNIQUE INDEX idx_disputes_open_per_purchase
    ON disputes(purchase_id) WHERE status = 'OPEN';  -- FD-13.10: 구매건당 진행중 분쟁 1개만


```sql
-- ============================================================
-- Device Tokens (FD-21.1 모바일 푸시 — v1.3 신설)
-- ============================================================
CREATE TABLE device_tokens (
    id              BIGSERIAL PRIMARY KEY,
    user_id         UUID NOT NULL REFERENCES users(user_id),
    device_token    VARCHAR(255) NOT NULL,
    platform        VARCHAR(10) NOT NULL CHECK (platform IN ('iOS','Android')),
    is_active       BOOLEAN NOT NULL DEFAULT TRUE,
    registered_at   TIMESTAMPTZ NOT NULL DEFAULT now()
    -- v1.3 재점검 라운드 정정: 원래 UNIQUE(user_id, device_token) 테이블 제약이었으나,
    -- 이러면 해지(is_active=false) 후 같은 토큰으로 재등록 시 INSERT 자체가 막힘
    -- (FD-21.1이 원래 의도한 "활성 상태 중복만 400"과 충돌). 활성 토큰끼리만
    -- 유니크하도록 부분 유니크 인덱스로 변경 — 해지된 행은 이력으로 남기고
    -- 재등록 시 새 행을 추가(또는 UPSERT로 재활성화, 착수 시 선택).
);
CREATE UNIQUE INDEX idx_device_tokens_active_unique
    ON device_tokens(user_id, device_token) WHERE is_active;
CREATE INDEX idx_device_tokens_user ON device_tokens(user_id) WHERE is_active;

-- notifications 테이블(아래)의 channel='PUSH' 발송 시 이 테이블 참조(FD-17.1 확장)


-- ============================================================
-- Strategy Executions (FD-16 전략 실행 제어판 — v1.1 신설)
-- ============================================================
CREATE TABLE strategy_executions (
    id                  BIGSERIAL PRIMARY KEY,
    strategy_id         VARCHAR(100) NOT NULL,
    strategy_version    VARCHAR(20) NOT NULL,
    user_id             UUID NOT NULL REFERENCES users(user_id),  -- 실행 컨텍스트 소유자
    exchange            VARCHAR(30) NOT NULL,
    mode                VARCHAR(10) NOT NULL CHECK (mode IN ('PAPER','LIVE')),
    allocated_capital   NUMERIC(20,2) NOT NULL,
    currency            VARCHAR(10) NOT NULL DEFAULT 'KRW',
    status              VARCHAR(20) NOT NULL DEFAULT 'PENDING_APPROVAL'
        CHECK (status IN ('PENDING_APPROVAL','RUNNING','PAUSED','RETIRED')),
    paused_by           VARCHAR(20)
        CHECK (paused_by IN ('USER','SAFETY_LAYER')),
        -- v1.3 후속(17번 프론트엔드 문서 검토 중 발견) — FD-16.3/9 재확인:
        -- Watchdog 자동정지와 사용자 수동정지를 status만으로는 구분 못 함
    retire_liquidation  VARCHAR(20)
        CHECK (retire_liquidation IN ('IMMEDIATE_MARKET','KEEP_POSITIONS')),
    converted_from_execution_id BIGINT REFERENCES strategy_executions(id),
        -- FD-16.5: PAPER→LIVE 전환 시 원본 PAPER 실행을 가리킴(이력 보존, 삭제 아님)
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    started_at          TIMESTAMPTZ,
    retired_at          TIMESTAMPTZ,
    FOREIGN KEY (strategy_id, strategy_version) REFERENCES strategies(strategy_id, version)
);
CREATE INDEX idx_strategy_executions_user ON strategy_executions(user_id);
CREATE INDEX idx_strategy_executions_status ON strategy_executions(status);

-- orders/positions에 execution_id 참조 추가(착수 시 반영)
ALTER TABLE orders ADD COLUMN execution_id BIGINT REFERENCES strategy_executions(id);
ALTER TABLE positions ADD COLUMN execution_id BIGINT REFERENCES strategy_executions(id);


-- ============================================================
-- Notifications (FD-17 알림 시스템 — v1.1 신설)
-- ============================================================
CREATE TABLE notifications (
    id              BIGSERIAL PRIMARY KEY,
    user_id         UUID NOT NULL REFERENCES users(user_id),
    event_type      VARCHAR(50) NOT NULL,   -- 'approval.request.created' 등
    channel         VARCHAR(20) NOT NULL CHECK (channel IN ('EMAIL','PUSH','IN_APP')),
    status          VARCHAR(20) NOT NULL CHECK (status IN ('SENT','FAILED')),
    payload_summary JSONB,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_notifications_user ON notifications(user_id, created_at DESC);

CREATE TABLE notification_preferences (
    user_id                     UUID PRIMARY KEY REFERENCES users(user_id),
    marketplace_purchase_email  BOOLEAN NOT NULL DEFAULT true,
    verification_result_email   BOOLEAN NOT NULL DEFAULT true,
    risk_mismatch_email         BOOLEAN NOT NULL DEFAULT true
    -- human_approval_requested, watchdog_triggered, execution_blocked는
    -- 컬럼 자체를 두지 않음 — 강제 채널이므로 애초에 끌 수 있는 여지를 스키마
    -- 레벨에서부터 없앤다(FD-17.4 서버측 거부의 이중 방어)
);
```

> `users`, `strategy_purchases`, `risk_profile_history` 테이블의 v1.1 변경분
> (적합성평가·탈퇴·중개수수료 컬럼)은 13번 문서(`13_multi_tenancy_auth.md`)
> §13.2/§13.5에 이미 병합됨 — 원 정의가 그쪽에 있으므로 여기서 중복 기재하지
> 않는다(이 문서 상단 원칙과 동일).

## §4.1 마이그레이션 원칙 ("0번부터 재검토" 라운드 — §4.X 표기로 정책문서 4장과 구분)

- 모든 스키마 변경은 Alembic(또는 팀 확정 도구)으로 버전 관리, ADR(정책문서 13.4)로 변경 이유 기록.
- `audit_log` 테이블은 애플리케이션 레벨 `UPDATE`/`DELETE` 권한을 원천 제거한다 (정책문서 16.3 P0-3 불변 감사로그 원칙의 실제 구현).
- Risk 수치(8.2-B Draft 8개 지표)는 이 스키마에 테이블로 두지 않고 `config/risk_policy.yaml` + Git 이력으로 관리 — 변경 시 반드시 인간 승인 커밋을 거치도록 CODEOWNERS로 보호한다(15.6-A FROZEN Zone 원칙과 연동).
