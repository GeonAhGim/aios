# 10. 구현 작업 트리 (Task Tree) 및 최소단위 커밋 관리 — v1.9

> **v1.9(2026-08-28) = 다자산군(Multi-Asset-Class) 확장 라운드.** ADR-2026-08-28
> 반영. 이 시점에 섹션 2(데이터 모델)·섹션 3(DB 스키마 기반 파트)·섹션 4(Event
> Bus)는 **이미 실제 코드로 구현·커밋된 상태**였다 — 01/04번 문서 개정이
> 기존 구현과 어긋나므로, 기존 리프를 소급 수정하지 않고 **새 리프를
> 추가**하는 방식으로 반영한다(이미 적용된 Alembic 마이그레이션을 되돌리지
> 않기 위함 — 3.20 참조). 신규 리프: 2.14(AssetClass/OptionType + Order/
> Position 파생 필드), 3.20(orders/positions 다자산군 컬럼 ALTER TABLE),
> 6.1의 ExchangeCapability 갱신(supported_asset_classes).
> 의존성을 전수조사 — `11.6`(탈퇴 API)만 유일하게 "16번 대분류 이후"라는
> 역방향 의존성이 있었음(나머지 17.1/18.1/18.3은 전부 앞→뒤 순방향이라
> 문제없음 확인). 11번 대분류 헤더에 이 예외를 명시적으로 강조.

> **v1.7(2026-08-10) = "구현자 리뷰 대조" 라운드.** `src/core/safety/base_loop.py`
> 신설(§16.0-B 대응 — 5개 안전장치 루프의 공통 예외방어 코드 위치).

> **v1.6(2026-08-10) = "클로드 코드 구현가능성" 검증 라운드.** ①정책문서
> 15.6-A가 명시한 `src/core/safety/`(Watchdog·Circuit Breaker·Data Distrust·
> Reconciliation, SCAFFOLD Zone)가 폴더트리에 없어 FD-9 전체의 구현 위치가
> 불명확했음 — 신설. ②`.aios-zone`/`CODEOWNERS`가 "만들어야 한다"는 작업
> 항목으로만 있고 실제 파일 포맷·내용이 어디에도 없었음 — 이게 없으면
> 어떤 AI 코딩 에이전트도 FROZEN 경로를 프로그래밍적으로 판별할 방법이
> 없었으므로 실제 YAML/CODEOWNERS 내용 작성.

> **v1.5(2026-08-10)**: `src/db/`(SQLAlchemy 세션+ORM 모델) 디렉토리 신설 —
> 16번이 `from src.db.session import get_db_session`을 계속 import해왔지만
> 이 디렉토리 자체가 폴더트리에도, 실제 ORM 모델 클래스도 어디에도 없었음을
> "실제 구현가능성 검증" 라운드에서 발견.

> **v1.4(2026-08-10) = "모든 문서 실제 구현가능성 검증" 라운드 — 폴더구조
> 근본 불일치 정정.** FD-11~21(대분류 11~21) 전체가 `src/core/{도메인}/`
> 구조로 배치돼 있었으나, 16번 백엔드 시그니처는 완전히 다른 구조
> (`src/api/routers/` + `src/services/`)를 쓰고 있었음 — 예: 이 문서는
> `src/core/execution_control/`인데 16번은 `src/services/execution_service.py`.
> 16번이 더 나중에 만들어진 실제 FastAPI 표준 패턴(라우터=검증만,
> 로직=Service 위임)이므로 이쪽을 진실로 삼아 폴더 트리 전면 재작성.
> `notifications/`, `indicators/`만 인프라·순수계산 성격이 강해 `src/core/`
> 유지, 나머지(auth/exchanges/marketplace/strategy_builder/suitability/
> execution/admin/portfolio/reports)는 `src/api/routers/` + `src/services/`
> 로 이동.

> **개정이력**: v1.0(기존, 대분류 1~10) → v1.1(2026-08-10, 대분류 11~21 추가) →
> v1.2(2026-08-10, 재점검 라운드: 3.14~3.16 DB 마이그레이션 리프 신설로 "테이블
> 1개=마이그레이션 1개=커밋 1개" 원칙 위반 정정 — strategy_executions/
> notifications/device_tokens가 API 작업 리프에 뭉쳐있던 것을 분리) →
> **v1.3(2026-08-10) = "0번부터 재검토" 라운드 — 번호체계 컨벤션 명시.**
> 아래 트리의 "대분류 N. 제목" 및 그 산하 "N.1, N.2..." 리프 번호는 **이
> 문서(10번) 자체의 작업트리 번호**이며, 정책문서(docx)의 장·절 번호(예:
> 정책문서 10장은 "사업모델"이고 10.1~10.6을 씀, 대분류 10 "인간 승인
> 흐름"의 10.1~10.4와 완전히 다른 주제)와 무관하다 — 15/16/17/13/04번에서
> 발견한 것과 같은 잠재적 혼동을 방지하기 위해 명시. 개별 트리 노드마다
> "§" 표기를 붙이면 가독성이 심하게 나빠지므로, 이 문서 전체에 대해
> "숫자만 보이면 작업트리 번호, 정책문서를 가리킬 때는 반드시 '정책문서'
> 또는 장 제목을 병기한다"는 규칙으로 대신한다(이 문서는 이미 대부분
> "정책문서 8.6-B"처럼 명시해왔음, 재확인 완료).
>
> 목적: 지금까지의 스펙(00~09)은 "모듈 단위"로 조직되어 있었다. 실제 구현 시에는
> 이보다 훨씬 작은 단위(함수 1개, 클래스 1개)로 쪼개야 이력 추적·리뷰·롤백이 쉽다.
> 이 문서는 그 최소 단위를 명시적인 트리로 정의하고, 각 리프 노드를 정확히
> 하나의 Git 커밋/PR/AIOSTask에 대응시킨다.

## 10.1 원칙

- **리프 노드 하나 = 커밋 하나 = PR 하나 = `AIOSTask` 하나(4.3).** 여러 리프를 한 커밋에 묶지 않는다.
- 리프 노드는 **독립적으로 테스트 가능**해야 한다 — 다른 미완성 리프에 의존해 테스트가 막히면 노드를 더 쪼갠다.
- 순서(①②③...)는 의존성 순서다 — 상위 번호가 하위 번호를 참조할 수 있지만 역방향은 안 된다.
- DevEngine이 작업할 경우, 이 트리의 리프 노드 하나가 16.2 Capability Token 하나의 `task_id`에 정확히 대응한다.

## 10.2 Phase 1 SCAFFOLD 전체 작업 트리

```
Phase 1 SCAFFOLD
│
├── 1. 프로젝트 기반
│   ├── 1.1 pyproject.toml + 의존성 고정 (16.5 Allowlist 최초 버전)
│   ├── 1.2 디렉터리 골격 생성 (§10.3 트리 그대로, __init__.py만)
│   ├── 1.3 .gitignore + .env.example (07번 §7.3)
│   └── 1.4 CODEOWNERS + .aios-zone 매니페스트 (15.6-A)
│
│   ### 1.4 실제 파일 내용 (신규 — "클로드 코드 구현가능성 검증" 라운드에서
│   ### 발견: 정책문서 15.6-A는 "매니페스트 태그를 명시한다"고만 선언하고
│   ### 실제 포맷을 정의한 적이 없었음 — 이 태그가 없으면 CI도, 어떤 AI
│   ### 코딩 에이전트도 FROZEN 경로를 프로그래밍적으로 판별할 방법이 없음.
│   ###
│   ### .aios-zone (저장소 루트, YAML) ###
│   ### zones:
│   ###   FROZEN:
│   ###     - "src/core/strategy/**"
│   ###     - "src/core/portfolio/**"
│   ###     - "src/core/risk/decision/**"      # 15.6-A: "매매 승인·거부 최종판단"만
│   ###     - "src/core/executor/**"
│   ###     - "aios/kernel/policy/**"           # Phase 4, 06번 §6.4 제외 대상
│   ###     - "aios/kernel/permission/**"       # Phase 4, 06번 §6.4 제외 대상
│   ###   SCAFFOLD:
│   ###     - "src/core/safety/**"              # 15.6-A: "감시"는 FROZEN 아님
│   ###     - "src/core/*/interfaces.py"
│   ###     - "src/data/models/**"
│   ###     - "src/exchanges/common/**"
│   ###     - "src/api/**"
│   ###     - "src/services/**"
│   ###     - "src/db/**"
│   ###   OPEN:
│   ###     - "docs/**"
│   ###     - "tests/**"
│   ###     - "config/**"
│   ###     - "scripts/**"
│   ###
│   ### CODEOWNERS (저장소 루트, GitHub 표준 포맷) ###
│   ### # FROZEN — 15.6-D 종료조건 충족 전까지 어떤 PR도 이 소유자 없이 병합 불가
│   ### /src/core/strategy/       @{owner}
│   ### /src/core/portfolio/      @{owner}
│   ### /src/core/risk/decision/  @{owner}
│   ### /src/core/executor/       @{owner}
│   ### # SCAFFOLD — 자동게이트 + 인간 리뷰어 1인(15.6-A)
│   ### /src/core/safety/         @{owner}
│   ### /src/api/                 @{owner}
│   ### /src/services/            @{owner}
│
├── 2. 데이터 모델 (01번 기반 — 파일 1개 = 커밋 1개보다 더 잘게, 클래스 단위)
│   ├── 2.1 base.py — ProvenanceStatus
│   ├── 2.1b base.py — Currency, Money, FXRate (11번 §11.1, 신규)
│   ├── 2.2 serialization.py — DecimalSafeEncoder (§1.6)
│   ├── 2.3 task.py — TaskStatus
│   ├── 2.4 task.py — AIOSTask (2.3 이후)
│   ├── 2.5 strategy_fsm.py — FSMState, FSMTransition
│   ├── 2.6 strategy_fsm.py — FSMStrategyConfig (2.5 이후)
│   ├── 2.7 market_data.py — Ticker
│   ├── 2.8 market_data.py — Candle, OrderBookLevel, OrderBook
│   ├── 2.9 trading.py — OrderStatus, OrderSide, OrderType (Enum 3개)
│   ├── 2.10 trading.py — Order (2.9 이후)
│   ├── 2.11 trading.py — Position, AccountBalance (2.1b Money 반영)
│   ├── 2.12 memory.py — MemoryType, MemoryEntry
│   ├── 2.12b exceptions.py — MihwaError 계층 (11번 §11.3, 신규)
│   ├── 2.13 각 모델 단위 테스트 (08번 §8.2 — 모델당 최소 1개 테스트 파일, 2.x와 쌍으로 커밋)
│   └── 2.14 base.py — AssetClass, OptionType (01번 §1.0, v1.4 신규, ADR-2026-08-28) +
│            trading.py의 Order/Position에 asset_class/option_type/strike_price/
│            expiry_date/contract_multiplier/underlying_symbol 필드 추가(기존
│            2.10/2.11 리프의 개정 — 이미 구현된 Order/Position 클래스를 수정) +
│            단위테스트 갱신
│
├── 3. DB 스키마 (04번 기반 — 테이블 1개 = 마이그레이션 1개 = 커밋 1개)
│   ├── 3.1 Alembic 초기화
│   ├── 3.2 tasks 테이블
│   ├── 3.3 capability_tokens 테이블 + tasks FK (3.2 이후)
│   ├── 3.4 strategies 테이블
│   ├── 3.5 strategy_memory_refs 테이블 (3.4, 3.9 이후 — memory_entries 먼저 필요)
│   ├── 3.6 orders 테이블
│   ├── 3.7 positions 테이블
│   ├── 3.8 reconciliation_events 테이블 (3.6, 3.7 이후)
│   ├── 3.9 memory_entries 테이블
│   ├── 3.10 audit_log 테이블 + WORM 제약(REVOKE)
│   ├── 3.11 users 테이블 확장 — risk_profile, risk_profile_assessed_at 컬럼
│   │        (FD-15.2, v1.1, 11.1 이후)
│   ├── 3.12 risk_profile_history 테이블 — 재평가 이력 보존 (3.11 이후, v1.1)
│   ├── 3.13 strategy_purchases 테이블 확장 — platform_commission_rate 등
│   │        P2P 중개수수료 컬럼 (13.7 이후, v1.1)
│   ├── 3.14 strategy_executions 테이블 (FD-16, v1.1, 3.4 strategies 이후) —
│   │        16.1 착수 전 선행 필요(10.1 원칙: 테이블 마이그레이션과 그 테이블을
│   │        쓰는 API 작업은 별도 리프)
│   ├── 3.15 notifications/notification_preferences 테이블 (FD-17, v1.1,
│   │        17.1 착수 전 선행 필요)
│   ├── 3.16 device_tokens 테이블 (FD-21.1, v1.3, 21.1 착수 전 선행 필요)
│   ├── 3.17 reviews 테이블 (FD-13.9, "0번부터 재검토" 라운드 신설, 13.9 착수 전 선행)
│   └── 3.18 disputes 테이블 (FD-13.10, "0번부터 재검토" 라운드 신설, 13.10 착수 전 선행)
│   ├── 3.19 withdrawal_whitelist 테이블 (FD-11.5, "다시 0번부터" 라운드 신설,
│   │        11.7 착수 전 선행)
│   └── 3.20 orders/positions ALTER TABLE — asset_class/option_type/strike_price/
│            expiry_date/contract_multiplier/underlying_symbol 컬럼 추가(전부
│            nullable, 04번 v1.7, ADR-2026-08-28). 기존 3.6/3.7 마이그레이션을
│            수정하지 않고 신규 마이그레이션으로 추가(이미 적용된 마이그레이션
│            보존 원칙, 10.1 원칙과 동일 정신 — 이력은 항상 순방향으로만 추가)
│
├── 4. Event Bus (05번 기반)
│   ├── 4.1 EventBus 추상 인터페이스
│   ├── 4.2 HandlerCriticality, EventBusPolicy (§5.5)
│   ├── 4.3 InProcessEventBus — publish/subscribe 기본 동작
│   ├── 4.4 InProcessEventBus — 백프레셔(큐 깊이 제한, §8.6)
│   ├── 4.5 InProcessEventBus — CRITICAL handler 에스컬레이션 경로
│   ├── 4.6 재시작 복구 절차 스크립트 (§5.6)
│   └── 4.7 Event Bus 통합 테스트 (08번 §8.3)
│
├── 5. Core 모듈 — SCAFFOLD (03번 기반, 함수 1개 = 커밋 1개)
│   ├── 5.1 Loader.load_config()
│   ├── 5.2 Loader.load_strategy_file() (2.6 이후)
│   ├── 5.3 Loader.load_env_secrets() + SecretBundle 마스킹
│   ├── 5.4 Parser.parse_ticker() (2.7 이후)
│   ├── 5.5 Parser.parse_candles() (2.8 이후)
│   ├── 5.6 Parser.parse_orderbook() (2.8 이후)
│   ├── 5.7 Validator.validate_order_params() (2.10 이후, v1.4부터 02번 §2.0-A
│   │        capability-gated 검증 — order.asset_class가 대상 거래소
│   │        supported_asset_classes에 없으면 UNSUPPORTED_ASSET_CLASS 거부
│   │        포함, ADR-2026-08-28)
│   ├── 5.8 Validator.validate_strategy_config() (2.6 이후)
│   ├── 5.9 Scanner.ScanCriteria + scan_market()
│   ├── 5.10 각 함수별 단위 테스트 (5.x와 쌍으로 커밋, 08번 §8.2)
│   └── 5.11 데이터 신뢰도 검증기 (FD-2.6, 8.1-A 히스테리시스 — 12번 기능설계에서 발견된 신규 리프)
│
├── 6. Exchange Adapter (02번 기반)
│   ├── 6.1 TickerCallback 타입 + ExchangeCapability + MarketHours (§9.1 반영본,
│   │        v1.4부터 ExchangeCapability.supported_asset_classes: list[AssetClass]
│   │        포함 — 02번 §2.0-A capability-gated 원칙, ADR-2026-08-28)
│   ├── 6.2 ExchangeAdapter 추상 클래스 (6.1 이후)
│   ├── 6.3 BitgetAdapter.__init__ + 인증
│   ├── 6.4 BitgetAdapter — Market Data 메서드군(get_ticker/orderbook/ohlcv) (6.3 이후)
│   ├── 6.5 BitgetAdapter — subscribe_ticker_stream + 재연결 로직 (6.4 이후)
│   ├── 6.6 BitgetAdapter — Account 메서드군(get_balance/get_positions)
│   ├── 6.7 BitgetAdapter — Trading 메서드군(place_order/cancel_order/modify_order/get_order)
│   ├── 6.8 BitgetAdapter — health_check()
│   ├── 6.9 KISAdapter.__init__ + OAuth 인증 (6.2 이후)
│   ├── 6.10 KISAdapter — Market Data + Account 메서드군(조회성만, §6.1 mvp_scope 원칙)
│   ├── 6.11 각 Adapter 통합 테스트 — Bitget Demo 실계정 (08번 §8.3)
│   └── 6.12 Capability Token 발급기 (FD-7.3, 16.2 — 12번 기능설계에서 발견된 신규 리프)
│
├── 7. 로깅·Config 실장 (07번 기반)
│   ├── 7.1 LogEntry 스키마 + 로거 초기화
│   ├── 7.2 risk_policy.yaml 최초 파일 생성(Draft 수치 그대로)
│   ├── 7.3 risk_policy 로더 + 스키마 검증 (7.2, 5.1 이후)
│   └── 7.4 audit_log 기록 유틸 (FD-7.2 — 12번 기능설계에서 발견된 신규 리프, 3.10 이후)
│
├── 8. Phase 1 SCAFFOLD 완료 검증 (06번 §6.3 Definition of Done 그대로 실행)
│   ├── 8.1 Watchdog 오탐 시뮬레이터 최초 실행 (통과여부 무관, 실행 자체가 조건 — 실제 판정 로직은 9.2)
│   ├── 8.2 지연 벤치마크 최초 측정
│   └── 8.3 20.1-A Go/No-Go 체크리스트 A그룹 상태 갱신
│
├── 9. 안전장치 계층 (신설 — 12번 §FD-9, Zone 재분류: FROZEN이 아니라 SCAFFOLD)
│   ├── 9.1 Watchdog 프로세스 골격 (별도 프로세스, 메인과 IPC로만 통신)
│   ├── 9.2 Watchdog 판정 로직 (정지/청산 분기, Griefing 방어 — FD-2.6 결과 참조)
│   ├── 9.3 Split-Brain 진단기 (히스테리시스 포함, 9.1 이후)
│   ├── 9.4 Circuit Breaker 상태기계
│   ├── 9.5 Data Distrust 쿼럼 확장 (5.11을 2소스→3소스 쿼럼으로 확장)
│   ├── 9.6 Reconciliation 에스컬레이션 로직 (3.8 이후)
│   ├── 9.7 Watchdog 오탐 검증 시뮬레이터 (9.2 이후, 06번 DoD 필수항목)
│   └── 9.8 통합 테스트 — Flash Crash 데이터 재생(9.7 이후)
│
├── 10. 인간 승인 흐름 (신설 — 12번 §FD-10)
│   ├── 10.1 승인 요청 워크플로 (60초 대기, FD-11.3 ApprovalMode에 따라
│   │        SOLO=1인/DUAL=이중서명 — 재점검 라운드에서 사용자 레벨임을 명확화)
│   ├── 10.2 SURGE 판정기 (Trigger Provenance 독립검증, 9.4/9.5 이후)
│   ├── 10.3 패닉 프롬프트 생성기 (화이트리스트 제약)
│   └── 10.4 통합 테스트 — SOLO 모드는 본인 1계정으로 즉시 테스트 가능(20.1-B
│            블로커와 무관), DUAL 모드는 Mock 승인자 2계정으로 테스트. 플랫폼
│            레벨(시스템 Kill Switch 등, FD-10과 별개)만 ADR-2026-08-10으로
│            1인 체제 조건부 확정된 상태.
│
├── 11. 인증·계정 (신규, FD-11 기반 — v1.0에서 기능설계문서에만 있던 것을
│         이번에 작업트리에 병합) — **주의: 11.6 하나만 예외적으로 16번
│         대분류(FD-16) 완료 후 착수**("흐름도 매끄러움 재점검" 라운드에서
│         발견 — 나머지 11.1~11.5/11.7은 순서 그대로 지금 착수 가능. 탈퇴
│         처리가 "RUNNING 실행 존재 여부"를 확인해야 하는데, 그 판정 대상인
│         strategy_executions 자체가 FD-16에서 만들어지기 때문)
│   ├── 11.1 users/user_approval_settings 마이그레이션 (13번 §13.2)
│   ├── 11.2 회원가입/로그인 API (Argon2id, 세션 토큰)
│   ├── 11.3 MFA(TOTP) 설정
│   ├── 11.4 승인설정(ApprovalMode) API — 60초 하한 CHECK 제약 포함
│   ├── 11.5 통합 테스트 — 로그인 실패 잠금, MFA 왕복
│   ├── 11.6 탈퇴 API (FD-11.4, v1.1 신규 — RUNNING 실행 존재 시 차단 검증 포함,
│   │        16번 대분류 이후)
│   └── 11.7 비상출금 화이트리스트 API (FD-11.5, "다시 0번부터" 라운드 신설 —
│            3.19 withdrawal_whitelist 마이그레이션 이후, FD-9 안전장치
│            상태조회 연동)
│
├── 12. 거래소 연동(사용자별) (신규, FD-12 기반)
│   ├── 12.1 exchange_credentials 마이그레이션 (13번 §13.3)
│   ├── 12.2 자격증명 등록 API (암호화 + 유효성 즉시검증)
│   ├── 12.3 자격증명 해지 API
│   ├── 12.4 Adapter 인증 로직 개정 (FD-3.1, 6.3/6.9 수정)
│   └── 12.5 통합 테스트 — 2사용자 동시조회 격리 검증(4.10 실증)
│
├── 13. 마켓플레이스 골격 (신규, FD-13 기반)
│   ├── 13.1 strategy_listings/strategy_purchases 마이그레이션 (13번 §13.5)
│   ├── 13.2 리스팅 API
│   ├── 13.3 검증 워크플로 (MVP 수동검증 브릿지)
│   ├── 13.4 구매 API (자전거래 방지 포함)
│   ├── 13.5 실행 연동 (owner_user_id vs 실행 컨텍스트 분리)
│   ├── 13.6 통합 테스트 — 판매자→구매자 데이터 비노출 검증(정책문서 10.3-B 실증)
│   ├── 13.7 P2P 중개수수료 계산 로직 (14번 마켓플레이스 상세 §14.1 갱신,
│   │        04번 스키마 확장 — v1.1 신규)
│   ├── 13.8 검색·정렬 API (FD-13.8, "0번부터 재검토" 라운드 신설 — 14번
│   │        §14.4가 처음부터 요구했으나 5라운드 동안 누락)
│   ├── 13.9 reviews 마이그레이션 + 리뷰 작성/조회 API (FD-13.9, "0번부터
│   │        재검토" 라운드 신설 — 14번 §14.2, 3.17 리프 참조)
│   ├── 13.10 disputes 마이그레이션 + 분쟁 접수 API (FD-13.10, "0번부터
│   │         재검토" 라운드 신설 — 14번 §14.5.1, 3.18 리프 참조)
│   └── 13.11 통합 테스트 — 검증통과일 정렬 실증, 1구매1리뷰 제약, 타인구매건
│             분쟁제기 차단
│
├── 14. 전략 편집기 (신규 — 12번 §FD-14, v1.1)
│   ├── 14.1 지표 계산 계층 (TA-Lib 연동, FD-2 캔들 파이프라인 이후)
│   ├── 14.2 전략 편집 UI (프론트엔드, 14.1 이후 — 지표 목록이 있어야 UI 구성 가능)
│   ├── 14.3 전략 저장·생애주기 연동 (3.4 strategies 테이블, 9.1 상태기계 이후)
│   ├── 14.4 프리뷰 계산기 (14.1 재사용)
│   └── 14.5 통합 테스트 — 조건조합→FSM 컴파일→9.11 스키마 검증 왕복
│
├── 15. 투자자 적합성평가 (신규 — 12번 §FD-15, v1.1)
│   ├── 15.1 적합성평가 설문 API (11.1 회원가입 이후, 온보딩 필수 게이트)
│   ├── 15.2 위험등급 스키마·API (04번 users 테이블 확장, 이력 보존 포함)
│   ├── 15.3 위험등급 매칭 경고 (13.4 구매 API, 14.3 배포승인, 11.4 ApprovalMode
│   │        변경 3개 지점에 훅으로 연동 — 15.2 이후)
│   └── 15.4 통합 테스트 — 안정형 사용자의 공격형 전략 구매 시도 시 경고+동의
│            흐름 검증
│
├── 16. 전략 실행 제어판 (신규 — 12번 §FD-16, v1.1)
│   ├── 16.1 자본배분 API (8.2-B 상한 검증 포함, 3.14 strategy_executions 이후)
│   ├── 16.2 실행모드 선택 API (FD-12.2, FD-10.1 연동)
│   ├── 16.3 실행 상태 제어 API (FD-9 안전장치 우선순위 검증 포함)
│   ├── 16.4 실행 모니터링 API (조회 전용)
│   ├── 16.5 통합 테스트 — Watchdog PAUSED 상태에서 사용자 시작 시도 거부 검증
│   └── 16.6 PAPER→LIVE 전환 API (FD-16.5, v1.1)
│
└── 17. 알림 시스템 (신규 — 12번 §FD-17, v1.1, **9·10보다 우선 착수 권장** —
          알림 인프라 없이는 9·10이 "발동은 하는데 아무도 모르는" 상태가 됨)
    ├── 17.1 알림 게이트웨이 (Event Bus 구독자로 구현, 3.15/4번 대분류 이후)
    ├── 17.2 채널 정책 테이블 (강제/선택 구분)
    ├── 17.3 알림 이력 API
    ├── 17.4 알림 설정 API (강제 항목 서버측 거부 포함)
    └── 17.5 통합 테스트 — 강제 채널 실패 시 audit_log 기록 검증

└── 18. 운영자 도구 (신규 — 12번 §FD-18, v1.2 최종 갭리뷰 라운드)
    ├── 18.1 검증 대기열 API (13번 대분류 이후)
    ├── 18.2 분쟁 처리 API (14번 문서 §14.5, 3.10 audit_log 이후)
    ├── 18.3 사용자 관리 API (11번 대분류 이후)
    ├── 18.4 판매자 정지 API (18.2 이후)
    ├── 18.5a 결제 대기 목록 API (FD-18.5a, 재점검 라운드 추가 — 15번 §15.1
    │        페이지네이션 적용)
    ├── 18.5b 결제 확인 API (FD-18.5b, 재점검 라운드 추가 — 13.4 실행연동
    │        트리거 지점, payment_status 컬럼 사용, Idempotency-Key +
    │        audit_log 기록 포함)
    └── 18.6 통합 테스트 — 이해상충 규칙(본인 리스팅 검증대기열 제외) +
             PENDING_PAYMENT 상태에서 실행권한 미부여 + 결제확인 중복요청
             시 audit_log 1건만 기록되는지 실증

├── 19. 포트폴리오 관리 (신규 — 12번 §FD-19, v1.3)
│   ├── 19.1 포트폴리오 조회 API (16.4 이후)
│   ├── 19.2 포트폴리오 재구성 API (16.1/16.2 재사용)
│   └── 19.3 통합 테스트 — 배분 감소 시 기존 포지션 미청산 검증
│
├── 20. 운용보고서 (신규 — 12번 §FD-20, v1.3)
│   ├── 20.1 보고서 집계 API (3.2/3.3, 9.4 재사용)
│   └── 20.2 보고서 화면 (프론트엔드)
│
└── 21. 모바일 앱 (신규 — 12번 §FD-21, v1.3, 크로스플랫폼)
    ├── 21.1 디바이스 토큰 API (3.16/17.1 확장)
    ├── 21.2 생체인증 연동 (11.2 확장, 클라이언트 로직 위주)
    └── 21.3 크로스플랫폼 앱 셸 구축 (React Native/Flutter, Draft —
             11~20 API 전체 재사용, 11~20 안정화 이후 착수 권장)
```

**착수 순서 권장(v1.3 최종)**: 1~8(기반) → 17(알림) → 9·10(안전장치·승인) →
11~13(계정·거래소·마켓플레이스) → 18(운영자 도구, 13 이후) → 14~16(편집기·
적합성평가·실행제어판) → 19~20(포트폴리오·보고서, 16 이후) → 21(모바일, API
전체 안정화 이후).

## 10.3 폴더 트리 (위 작업 트리와 1:1 대응하도록 세분화)

```
src/
├── api/                             # FastAPI 계층 — "모든 문서 실제 구현가능성
│   │                                # 검증" 라운드에서 발견: 이 폴더가 10번
│   │                                # 폴더트리에 아예 없었음(FD-11~21이 전부
│   │                                # src/core/{도메인}/에 배치돼 있었으나,
│   │                                # 16번 백엔드 시그니처는 완전히 다른 구조
│   │                                # (src/api/routers + src/services)를 씀 —
│   │                                # 16번이 더 나중에 만든 실제 FastAPI 표준
│   │                                # 패턴이라 이쪽을 진실로 삼아 10번을 정정)
│   ├── deps.py                     # §16.0
│   ├── exceptions.py                # MihwaError → HTTP 매핑
│   ├── main.py                     # §16.12, 앱 조립+라우터 등록
│   ├── schemas/
│   │   ├── __init__.py
│   │   └── base.py                 # CamelModel(§16.0)
│   └── routers/
│       ├── __init__.py
│       ├── auth.py                 # §16.1 (FD-11)
│       ├── exchanges.py             # §16.2 (FD-12)
│       ├── marketplace.py           # §16.3 (FD-13)
│       ├── strategy_builder.py      # §16.4 (FD-14)
│       ├── suitability.py           # §16.5 (FD-15)
│       ├── executions.py            # §16.6 (FD-16)
│       ├── notifications.py         # §16.7 (FD-17, API 부분만)
│       ├── admin.py                 # §16.8 (FD-18)
│       ├── portfolio.py             # §16.9 (FD-19)
│       ├── reports.py               # §16.10 (FD-20)
│       ├── devices.py               # §16.11 (FD-21)
│       └── disputes.py              # FD-13.10
│
├── services/                        # 비즈니스 로직 계층 — 라우터가 위임하는 대상
│   ├── __init__.py
│   ├── auth_service.py              # AuthService, AccountService (11.1~11.7)
│   ├── exchange_credential_service.py  # 12.1~12.6
│   ├── marketplace_service.py       # 13.1~13.10, DisputeService
│   ├── strategy_builder_service.py  # ConditionCompiler, StrategyBuilderService, IndicatorService (14.1~14.6)
│   ├── suitability_service.py       # SuitabilityService, MatchingWarningService (15.1~15.5)
│   ├── execution_service.py         # ExecutionService, RiskPolicyGate, ApprovalService, SafetyLayerStatusProvider (16.1~16.7)
│   ├── admin_service.py             # 18.1~18.6
│   ├── portfolio_service.py         # 19.1~19.4
│   └── report_service.py            # 20.1~20.3
│
├── db/                               # 신규(v1.4) — §16.0-A, 지금까지 어디에도
│   ├── __init__.py                  # 없었던 SQLAlchemy 세션/모델 계층
│   ├── base.py                      # Base(DeclarativeBase), §16.0-A
│   ├── session.py                   # engine, AsyncSessionLocal, get_db_session(), §16.0-A
│   └── models/
│       ├── __init__.py
│       ├── users.py                 # §16.0-A 대표 예시 작성 완료
│       ├── strategy_executions.py   # 나머지는 04/13번 DDL 기준 동일 패턴
│       ├── orders_positions.py
│       ├── notifications.py
│       ├── marketplace.py           # listings/purchases/reviews/disputes
│       └── withdrawal_whitelist.py
│
├── core/
│   ├── loader/
│   │   ├── __init__.py
│   │   ├── config_loader.py       # 5.1
│   │   ├── strategy_loader.py     # 5.2
│   │   └── secret_loader.py       # 5.3
│   ├── parser/
│   │   ├── __init__.py
│   │   ├── ticker_parser.py       # 5.4
│   │   ├── candle_parser.py       # 5.5
│   │   └── orderbook_parser.py    # 5.6
│   ├── validator/
│   │   ├── __init__.py
│   │   ├── order_validator.py     # 5.7
│   │   └── strategy_validator.py  # 5.8
│   ├── scanner/
│   │   ├── __init__.py
│   │   └── market_scanner.py      # 5.9
│   ├── event_bus/
│   │   ├── __init__.py
│   │   ├── bus.py                 # 4.1
│   │   ├── policy.py              # 4.2
│   │   ├── in_process.py          # 4.3, 4.4, 4.5
│   │   └── recovery.py            # 4.6
│   ├── logging/
│   │   ├── __init__.py
│   │   └── schema.py              # 7.1
│   ├── notifications/              # 17.1~17.4 — Event Bus 구독자라 인프라
│   │   ├── __init__.py             # 성격이 강해 services가 아닌 core에 유지
│   │   ├── gateway.py              # 17.1, §16.7 NotificationGateway
│   │   ├── channel_policy.py       # 17.2
│   │   └── preferences.py          # 17.4
│   ├── indicators/                 # 14.1 — TA-Lib 어댑터, 순수 계산 계층이라
│   │   ├── __init__.py             # core 유지(§16.4 IndicatorService가 이를 감쌈)
│   │   └── talib_adapter.py        # 14.1
│   ├── safety/                      # 신규(v1.5) — 정책문서 15.6-A가 명시한
│   │   │                            # SCAFFOLD Zone이지만 폴더트리에 없었음
│   │   │                            # ("클로드 코드 구현가능성 검증" 라운드
│   │   │                            # 발견 — FD-9 전체의 구현 위치가 불명확
│   │   │                            # 했음). "판단이 아니라 감시"이므로
│   │   │                            # FROZEN이 아니라 SCAFFOLD(15.6-A 명시).
│   │   ├── __init__.py
│   │   ├── base_loop.py             # 신규(v1.7) — 5개 루프 공통 예외방어
│   │   │                            # 패턴(§16.0-B, "구현자 리뷰 대조" 라운드
│   │   │                            # 발견 — 루프 예외 시 안전장치가 조용히
│   │   │                            # 죽는 것을 막는 코드가 없었음)
│   │   ├── watchdog.py              # FD-9.1/9.2
│   │   ├── split_brain.py           # FD-9.3
│   │   ├── circuit_breaker.py       # FD-9.4, FD-9.4b(재가동승인)
│   │   ├── data_distrust.py         # FD-9.5
│   │   └── reconciliation.py        # FD-9.6
│   └── strategy|portfolio|risk|executor/   # FROZEN — __init__.py + interfaces.py만
│            (15.6-D 전). risk/는 15.6-A 원문에 따라 두 부분으로 분리:
│            "매매 승인·거부 최종판단"만 FROZEN 유지, "8.2-B 지표 계산·감시"는
│            src/services/execution_service.py의 RiskPolicyGate(SCAFFOLD,
│            config/risk_policy.yaml 기반)로 이미 분리 구현됨 — §16.6 참조.
│
├── data/
│   └── models/
│       ├── __init__.py
│       ├── base.py                # 2.1
│       ├── serialization.py       # 2.2
│       ├── task.py                # 2.3, 2.4
│       ├── strategy_fsm.py        # 2.5, 2.6
│       ├── market_data.py         # 2.7, 2.8
│       ├── trading.py             # 2.9, 2.10, 2.11
│       └── memory.py              # 2.12
│
├── exchanges/
│   ├── common/
│   │   ├── __init__.py
│   │   ├── types.py                # 6.1
│   │   └── adapter.py              # 6.2
│   ├── bitget/
│   │   ├── __init__.py
│   │   ├── adapter.py              # 6.3
│   │   ├── market_data_mixin.py    # 6.4, 6.5 (가독성을 위해 Mixin으로 분리 — Draft)
│   │   ├── account_mixin.py        # 6.6
│   │   └── trading_mixin.py        # 6.7, 6.8
│   ├── kis/
│   │   ├── __init__.py
│   │   └── adapter.py              # 6.9, 6.10
│   └── bithumb/                    # 보류 — 기존 파일 유지, 신규 작업 없음
│
└── db/
    ├── migrations/                  # 3.1~3.10, Alembic 자동생성 파일들
    └── models/                      # SQLAlchemy 모델(선택) — Pydantic(01번)과 매핑

config/
├── risk_policy.yaml                 # 7.2
├── .env.example                     # 1.3
└── .env                             # 버전관리 제외

tests/
├── unit/
│   ├── data/models/                 # 2.13, 08번 §8.2와 동일 구조
│   └── core/                        # 5.10
├── integration/                     # 4.7, 6.11, 08번 §8.3
└── e2e/                             # 08번 §8.4
```

> **FD-21 모바일 앱(21.3)은 이 폴더 트리 밖, 별도 저장소로 분리한다** — 백엔드
> repo(AIOS 본체)와 React Native/Flutter 앱 repo를 분리하는 것이 20.1-B B그룹
> "AIOS 본체와 DevEngine을 별도저장소로 분리" 원칙과 동일 선상(클라이언트와
> 서버는 배포 주기·언어·CI가 다르므로). 앱 repo 구조는 21.3 착수 시 확정.

## 10.4 Git 커밋·브랜치 컨벤션

```
브랜치명:   scaffold/{작업트리번호}-{짧은설명}
예:        scaffold/2.4-aios-task-model
           scaffold/6.5-bitget-ticker-stream

커밋 메시지: [{작업트리번호}] {한 줄 요약}

             {필요시 본문 — 왜 이렇게 했는지}

             Spec: {참조 스펙 문서}#{섹션}
             Task-ID: {실제 착수 시 4.3 AIOSTask.task_id}

예:
[2.4] AIOSTask Pydantic 모델 구현

4.3 스키마를 1:1로 옮김. capability_token_id는 nullable로 유지
(3.3에서 FK 추가 예정이라 지금은 참조 불가).

Spec: 01_data_models.md#1.1
```

## 10.5 PR 크기 가이드라인

- **원칙**: 리프 노드 하나 = PR 하나. 여러 리프를 묶은 PR은 리뷰어가 반려한다.
- 목표 diff 크기: 200줄 이내(테스트 코드 포함해도). 초과하면 리프를 더 쪼갠다 — 이 트리 자체가 "더 쪼갤 수 없는 최소 단위"가 아니라 "지금 시점에 합리적인 최소 단위"이므로, 실제 구현하다 너무 크면 언제든 하위 노드를 추가한다.
- FROZEN Zone 경로(§10.3 `strategy|portfolio|risk|executor/`)를 건드리는 PR은 15.6-A 원칙에 따라 인터페이스 시그니처 변경만 허용 — 15.6-D 전까지 본문 구현 PR 자체를 열지 않는다.
- 각 PR 설명에 반드시 "Spec:" 라인으로 어느 스펙 문서·섹션을 구현한 것인지 명시 — 스펙과 코드가 어긋나면 항상 스펙을 먼저 갱신하고 코드 PR을 그에 맞춰 다시 낸다(공유접점문서 §4 변경관리 프로토콜과 동일 원칙).

## 10.6 진행 상황 추적

이 트리의 각 리프 노드는 실제 작업 시작 시 앞에 상태 마커를 붙여 관리한다(예: 이 파일을 그대로 체크리스트로 사용):

```
- [ ] 1.1 pyproject.toml + 의존성 고정
- [ ] 1.2 디렉터리 골격 생성
...
```

전체 완료 시 06번 §6.3 Definition of Done과 대조해 누락이 없는지 최종 확인한다.
