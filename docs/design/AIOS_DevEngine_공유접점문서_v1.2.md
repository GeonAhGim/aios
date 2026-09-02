# AIOS ↔ DevEngine 공유 접점 문서 (Shared Interface Contract) — v1.3

> **v1.3(2026-08-29)**: ADR-2026-08-29-E(FROZEN Zone 부분 개방 — PAPER
> 실행 전용) 반영 — §1.2 Zone 표에 **FROZEN-PAPER-ONLY** 중간 분류 신설,
> §1.4에 이 ADR 참조 추가. `src/core/strategy/`, `portfolio/`, `risk/`,
> `executor/`는 이제 PAPER 모드 판단·실행 로직에 한해 PR 가능(사람이
> 작성해도 15.6-A 예외 적용) — LIVE 경로는 여전히 코드 레벨로 하드
> 차단, 15.6-D 조건 2(실계정 MFA·이중승인) 충족 후 **별도 ADR**로만
> 해제 가능. `aios/kernel/policy/`, `permission/`(DevEngine 거버넌스
> 경로)은 이 변경 대상이 아니며 FROZEN 그대로 유지. **이 문서는 §4
> 절차상 AIOS·DevEngine 양쪽 지식 저장소에 동일하게 재업로드해야
> 한다** — 이 v1.3은 현재 AIOS 프로젝트에서만 갱신됐고, DevEngine
> 프로젝트 측 반영 여부는 별도 확인 필요.

> **v1.2(2026-08)**: ADR-2026-08-10-D(플랫폼 레벨 승인 게이트 정식 확정)
> 참조 추가 — DevEngine이 §2 동결 인터페이스를 수정하려는 시도는 이제
> ADR-2026-08-10-D §②의 승인 대상 결정유형 목록에 명시적으로 등재된
> 절차를 거쳐야 함을 명시.

> **v1.1(2026-08-10) = "0번부터 재검토" 라운드.** §0(상위문서 참조)과 §2.3
> (Order State Machine)을 실제 병합 완료 — 이전엔 patch-project-history-and-shared-interface-FINAL.md
> 로만 존재하고 본문 미반영이었음. **중요**: 이 문서는 §4 절차상 AIOS·DevEngine
> 양쪽 프로젝트 지식 저장소에 동일하게 업로드해야 하는 문서다 — 이 v1.1은
> 현재 AIOS 프로젝트에서만 갱신됐고, DevEngine 프로젝트 측 반영 여부는
> 별도 확인이 필요하다(공유접점문서 §4가 "한쪽만 갱신된 상태가 가장
> 위험한 시나리오"라고 스스로 명시).

> **이 문서는 "미화프로젝트-AIOS"와 "미화프로젝트-DevEngine" 두 프로젝트의 지식 저장소에 동일하게 업로드한다.**
> claude.ai Projects는 프로젝트 간 자동 참조·중첩을 지원하지 않으므로, 이 문서가 두 프로젝트를 이어주는 유일한 다리다.
> 이 문서의 내용과 실제 코드가 어긋나면 반드시 이 문서를 먼저 갱신하고 양쪽 프로젝트에 동시 재업로드한다 — 코드가 문서를 따르는 것이지 그 반대가 아니다.

---

## 0. 이 문서가 참조하는 상위 문서 (v1.1 병합 — "0번부터 재검토" 라운드에서
발견: patch-project-history-and-shared-interface-FINAL.md로만 존재하고
본문 미반영이었던 것 완결)

- `미화프로젝트_AIOS_개발문서_종합본_v3.4.docx` (정책·아키텍처 전체 — v3.4:
  8.10 Audit Log 조항 번호충돌 정정)
- `개발명세서/` 00~15번 + 기능설계문서(현재 v1.17, FD-1~21) + 16/17번
  (FastAPI 백엔드 시그니처·React 프론트엔드 아키텍처, 2026-08-10 세션 신설)

두 프로젝트 모두 위 문서 전체를 각자 지식 저장소에 넣을 필요는 없다 — **AIOS 프로젝트**는 종합본 전체 + 개발명세서를, **DevEngine 프로젝트**는 종합본 중 15·16장(발췌해도 무방) + 본 접점 문서를 넣는 것을 권장한다.

> **v1.2 병합 예정 — ADR-2026-08-10-D 반영**: 플랫폼 레벨 Critical Risk
> 승인(정책문서 4.9, DevEngine 거버넌스·16.2 Capability Token 발급정책·
> 16.6 메타통제면 변경 포함)이 ADR-2026-08-10(임시 1인체제)에서
> ADR-2026-08-10-D(정식 확정 — 5개 질문 가이드라인, 결정유형 목록,
> 180초 강제대기, 분기별 재검토)로 완결됐다. §2(동결 인터페이스)를
> DevEngine이 직접 수정하려는 시도는 §2의 승인 대상 결정유형 목록에
> 명시적으로 등재돼 있으니(ADR-2026-08-10-D §②), 이 절차를 반드시
> 거쳐야 한다 — DevEngine 프로젝트 측도 이 ADR을 인지해야 함.

---

## 1. 프로젝트 경계 원칙

### 1.1 물리적 분리 (15.2)
AIOS와 DevEngine은 별도 GitHub Repository, 별도 배포 파이프라인, 별도 인프라 계정을 쓴다. DevEngine 코드가 AIOS Repository에 직접 커밋되는 경로는 존재하지 않는다 — 유일한 통로는 PR이다.

### 1.2 AIOS Repository Zone 분류 (15.6-A)

| Zone | 범위 | DevEngine 접근 |
|---|---|---|
| **FROZEN** | `aios/kernel/policy/`, `aios/kernel/permission/` | 15.6-D 종료조건 충족 전까지 **어떤 PR도 대상 불가** (사람이 작성해도 동일) |
| **FROZEN-PAPER-ONLY**(v1.3 신설 — ADR-2026-08-29-E) | `src/core/strategy/`, `src/core/portfolio/`, `src/core/risk/`, `src/core/executor/` | PAPER 모드 판단·실행 로직에 한해 PR 가능(사람 작성 포함). **LIVE 경로는 코드 레벨로 하드 차단** — 15.6-D 조건 2(실계정 MFA·이중승인) 충족 후 별도 ADR로만 해제 |
| **SCAFFOLD** | 인터페이스·타입 정의, 데이터 모델, Exchange Adapter 시그니처, FD-4(주문 전송 계층) | 자동게이트+인간리뷰 1인 통과 시 병합 가능 |
| **OPEN** | docs/, tests/ 골격, configs/ | 표준 리뷰 |

### 1.3 SCAFFOLD 착수 트리거 게이트 (15.6-B, 16.4 P0-4로 강화)
DevEngine이 SCAFFOLD Zone에 PR을 내려면 먼저:
1. 자체 테스트 하네스에서 Red-Green-Refactor 연속 3회 이상 무개입 성공
2. 주입된 실패 테스트 1건 이상 무개입 복구
3. CI 게이트(SAST/의존성/Secret 스캔) 자체 저장소에서 실동작 확인
4. **(16.4 추가)** 프롬프트 인젝션·권한탈출·의도적 실패주입 등 적대적 인증 테스트셋 통과

### 1.4 FROZEN 개방 종료조건 (15.6-D)
아래 3가지가 모두 충족되어야 FROZEN-PAPER-ONLY가 완전 SCAFFOLD 수준(LIVE 포함)으로 재분류된다 — 이후에도 15.7 전체 게이트는 상시 적용:
1. 8.2-A Master Authority 구현 + 회귀테스트 통과
2. 4.9 Human Approval 보안요건(MFA·이중승인) 실제 운영 계정 적용 완료
3. 3.4/9.10 자율성 Level 3 Hard Gate 요건 충족

> **ADR-2026-08-29-E**: 조건 1·3은 PAPER 모드 범위에서 충족 가능(3은
> 현재의 "LIVE는 자동화 수준 무관 항상 승인 필요" 보수적 폴백으로 이미
> 과잉충족). 조건 2는 실거래소 키(Bitget/KIS)가 없으면 구조적으로
> 충족 불가능하므로, 이 ADR은 **PAPER 실행 전용으로만** FROZEN을
> FROZEN-PAPER-ONLY로 부분 재분류했다 — 위 1.2 표 참조. LIVE 전체
> 개방(진짜 SCAFFOLD 재분류)은 조건 2가 실제 충족된 뒤 별도 ADR
> 필요.

---

## 2. 동결된 인터페이스 계약 (15.6-C)

**아래 4개 스키마/인터페이스는 AIOS 설계 문서의 나머지 챕터가 계속 개정되어도 버전이 고정된다.** DevEngine은 이 계약만 보고 개발하며, 변경 시 반드시 양쪽에 동시 통지 + ADR 기록.

### 2.1 AIOSTask (기준: 개발명세서 §1.1)

```python
class AIOSTask(BaseModel):
    task_id: UUID
    parent_task_id: Optional[UUID]
    objective: str
    assigned_agent: str
    required_permission_level: int  # 0~6
    status: TaskStatus  # PENDING/ASSIGNED/RUNNING/WAITING/VERIFYING/COMPLETED/FAILED/CANCELLED/BLOCKED
    input_payload: dict
    output_result: Optional[dict]
    retry_count: int
    created_at: datetime
    completed_at: Optional[datetime]
    capability_token_id: Optional[UUID]  # §3.1 참조
```

### 2.2 FSMStrategyConfig (기준: 개발명세서 §1.2)

```python
class FSMStrategyConfig(BaseModel):
    strategy_id: str
    version: str
    target_asset: str
    market: str
    exchange: str
    initial_state: FSMState
    states: list[FSMState]
    transitions: list[FSMTransition]
    author_agent: str
    memory_provenance: list[UUID]  # 4.6-A Memory-Strategy 출처 연결
```

### 2.3 Order State Machine (기준: 개발명세서 §1.4)

```
CREATED → VALIDATED → SUBMITTED → ACKNOWLEDGED → PARTIALLY_FILLED → FILLED
예외 상태: REJECTED / CANCELLED / EXPIRED / FAILED / UNKNOWN
```
UNKNOWN 상태는 절대 실패로 단정하지 않는다 — 반드시 거래소 실제 상태 재조회.

> **ADR-2026-08-10-C 반영(v1.1 병합)**: `Order`/`Position` 모델에
> `execution_id: Optional[int]` 필드 추가(FD-16 전략 실행 제어판 신설에
> 따른 하위호환 확장 — 상태 전이 규칙 자체는 변경 없음). DevEngine이 이
> 모델을 참조하는 코드를 생성할 때 이 필드를 인지해야 한다. 01번 데이터모델
> v1.2에 이미 실제 반영됨.

### 2.4 Exchange Adapter 공통 인터페이스 (기준: 개발명세서 §2)

```python
class ExchangeAdapter(ABC):
    def get_capabilities(self) -> ExchangeCapability: ...
    async def get_ticker(self, symbol: str) -> Ticker: ...
    async def get_orderbook(self, symbol: str, depth: int = 20) -> OrderBook: ...
    async def get_ohlcv(self, symbol: str, timeframe: str, limit: int = 100) -> list[Candle]: ...
    async def get_balance(self, asset: str | None = None) -> list[AccountBalance]: ...
    async def get_positions(self, symbol: str | None = None) -> list[Position]: ...
    async def get_order(self, order_id: str) -> Order: ...
    async def place_order(self, order: Order) -> Order: ...
    async def cancel_order(self, order_id: str) -> bool: ...
    async def modify_order(self, order_id: str, **kwargs) -> Order: ...
    async def health_check(self) -> bool: ...
    # 출금(withdraw) 메서드는 원천적으로 존재하지 않는다 — 7.3/7.10 원칙
```

> v3.1 갱신 — Phase 1 활성 거래소: **Bitget(crypto) + 한국투자증권/KIS(kr_equity)**. Bithumb·SK증권은 보류.
> KIS는 `asset_class="kr_equity"`, 09:00-15:30 KST 개장 — 8.6-A Cross-Asset Time-Gap Buffer 대상. Bitget(24시간)과 동시 실거래는 FROZEN Zone Cross-Asset 로직 구현 전까지 금지, Adapter 인터페이스 개발(SCAFFOLD) 자체는 지금 가능.

---

## 3. DevEngine → AIOS 접근 통제 (16장 요약)

### 3.1 Task-scoped Capability Token (16.2)
DevEngine의 모든 개별 작업은 전역 권한과 별개로 이 토큰을 발급받아야 실행된다.

```
필수 필드: token_id, task_id, repository, branch, paths, operations, network, secrets, ttl
```
- `paths`는 발급 시점에 §1.2 Zone 분류와 자동 대조 — FROZEN 경로 포함 토큰은 발급 자체가 거부됨
- 단일 Task에만 유효, 완료/TTL만료/취소 시 즉시 무효화

### 3.2 메타 통제면 불변 원칙 (16.6, 가장 중요)
DevEngine은 아래 컴포넌트에 대해 **어떤 경우에도 Write Capability를 발급받을 수 없다**:

> Policy Engine, Permission Engine, Capability Token 검증 로직, Audit System, Approval System, Sandbox Boundary, Credential Boundary, Kill Switch, Budget Hard Limit, Identity/Auth, GitHub Security Boundary

이 차단은 애플리케이션 로직이 아니라 **IAM 권한 자체의 물리적 부재**로 구현한다 — DevEngine 서비스 계정에 위 컴포넌트 접근 권한이 인프라 수준에서 아예 없어야 한다.

### 3.3 Fail-Closed 원칙 (16.7, 적용범위 주의)
Policy/Capability/Audit/Sandbox/Credential/Budget/Approval 상태가 UNKNOWN·TIMEOUT·ERROR면 예외 없이 DENY.

> ⚠️ **이 원칙은 DevEngine에만 적용된다.** AIOS의 실시간 거래 경로(Risk Engine 등)에 그대로 적용하면 안 된다 — AIOS 쪽은 8.2-A Master Authority·8.6-A-1(정지/청산 분리) 등 별도의 더 정교한 원칙을 따른다. 두 프로젝트는 이 지점에서 원칙이 다르다는 것을 서로 인지하고 있어야 한다.

### 3.4 의존성 공급망 게이트 (16.5)
Allowlist 확인 → Version Pinning → Vulnerability Scan → Human Approval(Allowlist 확장은 DevEngine이 스스로 할 수 없음).

---

## 4. 변경 관리 프로토콜

1. 이 문서(§2 동결 인터페이스, §3 접근 통제)를 변경해야 하는 상황이 생기면, 어느 프로젝트에서 발견했든 즉시 ADR(13.4 양식)로 기록한다.
2. 변경안은 양쪽 프로젝트 대화에서 각각 검토한다 — 한쪽에서만 결정하지 않는다.
3. 합의된 변경은 이 문서를 갱신하고, **양쪽 프로젝트 지식 저장소에 동시에 재업로드**한다. 한쪽만 갱신된 상태로 개발이 진행되는 것이 가장 위험한 시나리오다.
4. 상위 종합본 문서(v3.0)에도 반영이 필요한 변경이면 별도로 그 문서도 갱신한다.

---

## 5. 각 프로젝트 지침(Custom Instructions) 제안 문구

### AIOS 프로젝트용
```
너는 미화프로젝트의 AIOS(투자 운영체제 본체) 개발을 담당한다.
- 이 프로젝트 지식 저장소의 종합본 v3.0과 개발명세서를 항상 최우선 근거로 삼는다.
- src/core/의 FROZEN Zone(Strategy/Portfolio/Risk/Executor)은 15.6-D 종료조건
  충족 전까지 실제 판단 로직을 작성하지 않는다 — 인터페이스만 다룬다.
- DevEngine과의 접점은 반드시 "AIOS-DevEngine 공유 접점 문서"를 따르고,
  이 문서의 §2(동결 인터페이스)를 임의로 변경하지 않는다.
- Risk/Kill Switch 등 안전장치 관련 수치는 전부 Draft이며 인간 승인 없이 확정하지 않는다.
```

### DevEngine 프로젝트용
```
너는 미화프로젝트의 DevEngine(AIOS를 개발하는 자율개발 도구) 개발을 담당한다.
- 이 프로젝트 지식 저장소의 "AIOS-DevEngine 공유 접점 문서"와 종합본 15·16장을
  항상 최우선 근거로 삼는다.
- AIOS Repository의 FROZEN Zone에는 어떤 코드도 제안하지 않는다.
- 16.6 메타 통제면 불변 원칙 대상(Policy/Permission/Audit/Approval/Kill Switch/
  Credential/Budget/Auth/GitHub Security Boundary)에는 Write 권한을 요구하거나
  가정하지 않는다.
- Fail-Closed 원칙(16.7)은 DevEngine 자신에게 적용하고, AIOS 실시간 거래 경로에는
  이 원칙을 적용하지 않는다 — 그쪽은 별도 원칙(Master Authority)을 따른다.
- 공유 접점(§2 스키마)을 변경해야 할 필요를 발견하면, 스스로 결정하지 말고
  "AIOS-DevEngine 공유 접점 문서" §4 변경관리 프로토콜을 따르도록 사용자에게 요청한다.
```
