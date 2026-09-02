# 17. 프론트엔드 아키텍처 (React + TypeScript, 모바일 공유 전제) — v1.7

> 근거: ADR-2026-08-10-B(기술스택), architecture-vision-crosscheck.md(화면 15개
> 목록), 16번 문서(백엔드 시그니처 — 이 문서의 모든 API 타입은 16번의 Pydantic
> 모델을 TypeScript로 1:1 대응시킨 것)
> **v1.0(2026-08-10)**: 최초 작성.
> **v1.1(2026-08-10)**: `pausedBy` 필드 처리 갱신(16번 v1.1 대응), 직렬화
> 컨벤션(CamelModel) 확정 명시.
> **v1.2(2026-08-10)**: MFA 필수 게이트 반영 — 온보딩 순서에 `/onboarding/mfa-setup`
> 삽입(정책문서 §4.10 "MFA는 사용자 레벨에서도 예외 없이 강제" 반영).
> **v1.3(2026-08-10)**: (버전 헤더 갱신 누락 — 내용은 v1.2에 포함되어 실질
> 변경 없음, 재점검 라운드에서 발견해 정정)
> **v1.4(2026-08-10) = "0번부터 재검토" 라운드.** apiClient에 리뷰/분쟁/거래소등록
> 메서드가 실제로 정의된 적 없었음(WriteReviewPage/DisputeSubmitPage가 호출할
> 대상 부재) 발견해 추가. `WITHDRAWAL_PERMISSION_DETECTED` 전용 에러 처리
> 추가(FD-12.1). 운영자 화면 2개 신설 — `PendingPaymentsPage`(FD-18.5a/b),
> `ReactivationApprovalPage`(FD-9.4b, Circuit Breaker 재가동승인).
> **v1.5(2026-08-10) = 번호 충돌 정정.** 이 문서의 최상위 섹션(舊 "## 17.1"
> 등)이 정책문서(docx) 17장(레드팀 검토, 17.1~17.14)과 번호가 겹치는 것을
> 발견 — 16번 문서와 동일 라운드에 동일 조치. 모든 최상위 헤더를 "§17.X"로
> 전면 변경(이 문서 자신은 §17.X, 접두어 없는 17.X는 정책문서).
> 상태: 전체 SCAFFOLD-READY
> **직렬화 컨벤션(재점검 라운드에서 확정)**: 16번 문서 §16.0의 `CamelModel`이
> Python 내부 snake_case를 JSON camelCase로 자동 변환한다 — 이 문서의 모든
> TypeScript camelCase 필드명은 별도 변환 코드 없이 API 응답과 그대로 맞는다.
> 원칙: 웹(React)과 모바일(FD-21, React Native)이 **API 클라이언트·타입·상태관리
> 훅을 하나의 공유 패키지로 재사용**한다 — 같은 로직을 두 번 안 짠다.
> **v1.7(2026-08-28) = 다자산군(Multi-Asset-Class) 확장 라운드.** ADR-2026-08-28
> 반영 — `StrategyCreateRequest` 타입에 파생상품 Optional 필드 추가(16번 v1.10
> 대응), `apiClient.getExchangeCapabilities(exchange)` 메서드 신규(전략
> 편집기가 거래소별 지원 자산군을 조회해 UI를 조건부 렌더링하기 위함). 실제
> 옵션체인/선물 만기 캘린더 등 자산군 특화 화면 설계는 해당 자산군의
> Adapter가 실제 착수될 때(06번 §6.1-A) 별도 라운드에서 다룬다 — 지금은
> 타입 동기화만.

## §17.0 모노레포 구조 — 웹·모바일 공유 전제

```
apps/
├── web/                    # React + Vite, 이 문서 §17.1~17.6
└── mobile/                 # React Native, FD-21, 별도 문서(17.9)

packages/
├── api-client/             # 16번 API 전체의 TypeScript 클라이언트 — web·mobile 공용
├── shared-types/           # 16번 Pydantic 모델과 1:1 대응하는 TS 타입
├── shared-hooks/           # useAuth, usePortfolio 등 — web·mobile 공용 비즈니스 로직
└── ui-web/                 # 웹 전용 디자인시스템 컴포넌트 (모바일은 RN 네이티브 컴포넌트 별도)
```

**왜 모노레포인가**: FD-21이 "새 백엔드 기능이 아니라 같은 API를 소비하는 두 번째
클라이언트"로 설계됐다(기능설계문서 FD-21 근거). 이 설계를 프론트엔드 구조에도
그대로 반영 — `api-client`/`shared-types`/`shared-hooks` 3개 패키지가 웹과
모바일 양쪽에서 동일하게 import된다. 도구: Turborepo 또는 Nx(Draft, 착수 시 확정).

---

## §17.1 apps/web 폴더 구조

```
apps/web/src/
├── routes/                          # §17.3 라우팅 테이블과 1:1
│   ├── auth/
│   │   ├── SignupPage.tsx
│   │   ├── LoginPage.tsx
│   │   └── MfaSetupPage.tsx
│   ├── onboarding/
│   │   └── RiskAssessmentPage.tsx   # FD-15.1, 필수 게이트
│   ├── dashboard/
│   │   └── DashboardPage.tsx
│   ├── exchanges/
│   │   └── ExchangeManagementPage.tsx
│   ├── strategy-builder/
│   │   ├── StrategyBuilderPage.tsx
│   │   └── components/
│   │       ├── IndicatorPicker.tsx
│   │       ├── ConditionBuilder.tsx
│   │       └── PreviewChart.tsx
│   ├── strategies/
│   │   └── MyStrategiesPage.tsx     # 9.1 생애주기 상태 표시
│   ├── marketplace/
│   │   ├── MarketplaceBrowsePage.tsx
│   │   ├── ListingDetailPage.tsx    # 구매 + FD-15.3 경고 모달 여기서 발동
│   │   └── SellStrategyPage.tsx
│   ├── executions/
│   │   └── ExecutionControlPage.tsx # FD-16 시작/일시정지/중지
│   ├── portfolio/
│   │   └── PortfolioPage.tsx        # FD-19
│   ├── reports/
│   │   └── ReportsPage.tsx          # FD-20
│   ├── approvals/
│   │   └── ApprovalPromptPage.tsx   # FD-10, 60초 대기 UI
│   ├── settings/
│   │   ├── ApprovalSettingsPage.tsx
│   │   ├── NotificationSettingsPage.tsx
│   │   └── AccountDeletionPage.tsx
│   ├── audit/
│   │   └── AuditLogPage.tsx
│   ├── reviews/
│   │   └── WriteReviewPage.tsx
│   ├── disputes/
│   │   └── DisputeSubmitPage.tsx
│   └── admin/                       # FD-18, is_platform_admin 가드
│       ├── VerificationQueuePage.tsx
│       ├── DisputeManagementPage.tsx
│       ├── UserManagementPage.tsx
│       ├── PendingPaymentsPage.tsx   # FD-18.5a/b, "0번부터 재검토" 라운드 추가
│       └── ReactivationApprovalPage.tsx  # FD-9.4b, "0번부터 재검토" 라운드
│                                       # 추가 — Circuit Breaker halted/emergency
│                                       # 재가동 승인(플랫폼 레벨, Draft)
├── components/                      # 여러 라우트가 공유하는 컴포넌트
│   ├── layout/AppShell.tsx
│   ├── RiskWarningModal.tsx         # FD-15.3 — 마켓플레이스·편집기·설정 3곳에서 재사용
│   └── ApprovalPendingBanner.tsx    # FD-10, 어느 화면에서든 상단 고정 노출.
│                                     # "0번부터 재검토" 라운드 확인: FD-9.4b
│                                     # (Circuit Breaker 재가동승인)도 같은
│                                     # ApprovalRequest 구조를 재사용하므로
│                                     # 이 배너가 운영자 화면에서 함께 처리
├── router.tsx                       # §17.3
└── main.tsx
```

---

## §17.2 API 클라이언트 & 공유 타입 — `packages/shared-types/`, `packages/api-client/`

```typescript
// packages/shared-types/src/strategy.ts
// STATUS: SCAFFOLD-READY
// 16번 문서 §16.4 StrategyCreateRequest/StrategyResponse와 1:1 대응

export interface IndicatorSpec {
  name: string;
  params: Record<string, number>;
}

export interface ConditionSpec {
  indicator: IndicatorSpec;
  operator: "<" | ">" | "<=" | ">=";
  value: number;
}

export interface StrategyCreateRequest {
  targetAsset: string;
  exchange: string;
  entryCondition: ConditionSpec;
  exitCondition: ConditionSpec;
  // v1.7(ADR-2026-08-28) 다자산군 확장 — 크립토/현물 전략은 전부 undefined 유지.
  assetClass?: AssetClass;       // 16번 v1.10 참조, undefined면 서비스가 CRYPTO로 간주
  optionType?: "CALL" | "PUT";
  strikePrice?: number;
  expiryDate?: string;           // ISO date
  underlyingSymbol?: string;
  stopLossPct?: number;
}

export type LifecycleStatus =
  | "IDEA" | "RESEARCH"  // 재점검 라운드에서 누락 발견 — AI 생성 전략(9.2)은
                          // 이 단계부터 시작 가능. 사용자 편집기(FD-14.3)는
                          // GENERATED부터 시작하므로 이 두 값을 안 볼 수도
                          // 있지만, 타입 자체에서 빠지면 안 됨.
  | "GENERATED" | "BACKTESTING" | "VALIDATING" | "STRESS_TESTING"
  | "RISK_REVIEW" | "PAPER_TRADING" | "APPROVED" | "DEPLOYED"
  | "MONITORING" | "REVIEW" | "RETIRED" | "REJECTED" | "FAILED";
  // 04번 DB스키마 lifecycle_status CHECK 제약과 1:1 — 여기서 값이 어긋나면
  // 백엔드 400을 그대로 신뢰(프론트가 임의로 재해석하지 않는다)

export interface StrategyResponse {
  strategyId: string;
  version: string;
  status: LifecycleStatus;
  fsmDefinition: unknown; // 9.11 FSMStrategyConfig — 편집기가 재조립할 일 없음(블랙박스로 취급)
}
```

```typescript
// packages/api-client/src/client.ts
// STATUS: SCAFFOLD-READY
import type { StrategyCreateRequest, StrategyResponse } from "@aios/shared-types";

export class ApiError extends Error {
  constructor(public statusCode: number, public errorCode: string, message: string) {
    super(message);
  }
}

export class AiosApiClient {
  constructor(private baseUrl: string, private getToken: () => string | null) {}

  private async request<T>(path: string, init?: RequestInit): Promise<T> {
    /** 모든 API 호출의 단일 관문 — 토큰 첨부, 15.3(표준 에러 응답 포맷) 파싱을
     * 여기서 일괄 처리한다. 각 도메인 메서드가 fetch를 직접 호출하지 않는다. */
    ...
  }

  // ---- FD-14 전략 편집기 ----
  async createStrategy(body: StrategyCreateRequest): Promise<StrategyResponse> {
    return this.request("/strategies", { method: "POST", body: JSON.stringify(body) });
  }

  async previewStrategy(strategyId: string): Promise<unknown> {
    return this.request(`/strategies/${strategyId}/preview`);
  }

  // ---- FD-16 실행 제어판 ----
  async createExecution(body: unknown): Promise<unknown> { return this.request("/strategy-executions", { method: "POST", body: JSON.stringify(body) }); }
  async startExecution(executionId: string): Promise<unknown> { return this.request(`/strategy-executions/${executionId}/start`, { method: "POST" }); }
  async pauseExecution(executionId: string): Promise<unknown> { return this.request(`/strategy-executions/${executionId}/pause`, { method: "POST" }); }

  // ---- FD-19 포트폴리오 ----
  async getPortfolio(): Promise<unknown> { return this.request("/portfolio"); }
  async rebalancePortfolio(body: unknown): Promise<unknown> { return this.request("/portfolio/rebalance", { method: "PUT", body: JSON.stringify(body) }); }

  // ---- FD-12 거래소 연동 ("0번부터 재검토" 라운드 추가 — WITHDRAWAL_PERMISSION_DETECTED
  //      전용 에러 처리가 필요해 명시적으로 뽑아둠, 나머지 메서드와 달리 요약 안 함) ----
  async registerExchangeCredential(body: unknown): Promise<unknown> {
    try {
      return await this.request("/exchange-credentials", { method: "POST", body: JSON.stringify(body) });
    } catch (e) {
      if (e instanceof ApiError && e.errorCode === "WITHDRAWAL_PERMISSION_DETECTED") {
        /** FD-12.1 재점검 라운드 추가 — 일반 에러 토스트가 아니라 "이 키는
         * 출금 권한이 있어 등록할 수 없습니다. 거래소에서 출금 권한을
         * 제외하고 재발급해주세요" 전용 안내 화면으로 분기(ExecutionCard의
         * EXECUTION_BLOCKED_BY_SAFETY_LAYER 처리와 동일한 패턴 — 재시도해도
         * 안 되는 상황임을 명확히 전달). */
      }
      throw e;
    }
  }

  // ---- v1.7 다자산군 확장(ADR-2026-08-28) ----
  async getExchangeCapabilities(exchange: string): Promise<ExchangeCapability> {
    /** 16번 v1.10 GET /exchange-credentials/{exchange}/capabilities 대응.
     * 전략 편집기(StrategyBuilderPage)가 이 결과의 supportedAssetClasses를
     * 보고 옵션/선물 전용 입력 필드를 조건부로 렌더링한다. */
    return this.request(`/exchange-credentials/${exchange}/capabilities`);
  }

  // ---- FD-13.9/13.10 리뷰·분쟁 ("0번부터 재검토" 라운드 추가 — WriteReviewPage/
  //      DisputeSubmitPage가 호출할 메서드가 없었음) ----
  async createReview(listingId: number, body: unknown): Promise<unknown> {
    return this.request(`/marketplace/listings/${listingId}/reviews`, { method: "POST", body: JSON.stringify(body) });
  }
  async submitDispute(body: unknown): Promise<unknown> {
    return this.request("/disputes", { method: "POST", body: JSON.stringify(body) });
  }

  // (나머지 FD-11/15/17/18/20/21 동일 패턴 — 16번 문서 라우터 1:1 대응,
  //  전체 메서드 목록은 착수 시 16번 문서 순회하며 기계적으로 생성 가능)
}
```

---

## §17.3 라우팅 테이블 (15개 화면 + 신규 4개, React Router)

| 경로 | 컴포넌트 | 가드 | 근거 |
|---|---|---|---|
| `/signup`, `/login` | SignupPage/LoginPage | 없음 | FD-11.1 |
| `/onboarding/mfa-setup` | MfaSetupPage | 로그인+`mfa_enabled=false` 시 강제 리다이렉트, 완료 전까지 다른 화면 진입 불가(재점검 라운드 추가 — 정책문서 §4.10 "MFA는 사용자 레벨에서도 예외 없이 강제" 반영, MFA가 선택에서 필수로 정정되며 온보딩 순서에 신규 삽입) | FD-11.2 필수 게이트 |
| `/onboarding/risk-assessment` | RiskAssessmentPage | 로그인+MFA 완료+적합성평가 미완료 시 강제 리다이렉트 | FD-15.1 필수 게이트 |
| `/dashboard` | DashboardPage | 로그인+MFA+적합성평가 완료 | FD-3.2, 7.2 |
| `/exchanges` | ExchangeManagementPage | 로그인 | FD-12 |
| `/strategy-builder` | StrategyBuilderPage | 로그인 | FD-14 |
| `/strategies` | MyStrategiesPage | 로그인 | 9.1 상태 |
| `/marketplace` | MarketplaceBrowsePage | 없음(비로그인도 탐색 가능, Draft) | FD-13.1 |
| `/marketplace/:listingId` | ListingDetailPage | 구매는 로그인 필요 | FD-13.3 |
| `/marketplace/sell` | SellStrategyPage | 로그인 | FD-13.1/13.2 |
| `/executions` | ExecutionControlPage | 로그인 | FD-16 |
| `/portfolio` | PortfolioPage | 로그인 | FD-19 |
| `/reports` | ReportsPage | 로그인 | FD-20 |
| `/approvals/:requestId` | ApprovalPromptPage | 로그인, 딥링크(이메일/푸시에서 진입) | FD-10.1 |
| `/settings/approval` | ApprovalSettingsPage | 로그인 | FD-11.3 |
| `/settings/notifications` | NotificationSettingsPage | 로그인 | FD-17.4 |
| `/settings/account` | AccountDeletionPage | 로그인 | FD-11.4 |
| `/audit-log` | AuditLogPage | 로그인 | FD-7.2 |
| `/reviews/write/:purchaseId` | WriteReviewPage | 로그인, 구매 후 30일 경과(14.2.3) | 14.2.3 |
| `/disputes/submit` | DisputeSubmitPage | 로그인 | 14.5 |
| `/admin/*` | Admin* 3종 | `is_platform_admin`(관리자만) | FD-18 |
| `/admin/pending-payments` | PendingPaymentsPage | `is_platform_admin`("0번부터 재검토" 라운드 추가) | FD-18.5a/b |
| `/admin/reactivation-approvals` | ReactivationApprovalPage | `is_platform_admin`("0번부터 재검토" 라운드 추가) | FD-9.4b |

`/approvals/:requestId`는 FD-17 알림(이메일/푸시)의 링크가 직접 가리키는
딥링크 — 사용자가 알림을 받고 바로 승인 화면으로 들어올 수 있어야 한다(4.9
60초 타이머의 실효성이 여기 달려 있음, 알림→클릭→승인까지 지연이 짧아야 함).

---

## §17.4 상태관리 패턴 — TanStack Query + Zustand

```typescript
// packages/shared-hooks/src/useExecutions.ts
// STATUS: SCAFFOLD-READY
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { apiClient } from "./client-instance";

/** 서버 상태(실행 목록 등)는 TanStack Query로만 다룬다 — 별도 전역 상태에
 * 중복 저장하지 않는다(캐시 무효화 전략을 이원화하지 않기 위함). */
export function useExecutions() {
  return useQuery({
    queryKey: ["executions"],
    queryFn: () => apiClient.listExecutions(),
    refetchInterval: 5000, // FD-16.4 실시간성 요구 — 5초 폴링(Draft, WebSocket 전환 검토 대상)
  });
}

export function useStartExecution() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (executionId: string) => apiClient.startExecution(executionId),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["executions"] }),
    onError: (err) => {
      /** FD-16.3 예외상황(EXECUTION_BLOCKED_BY_SAFETY_LAYER, 409) —
       * 이 에러코드는 일반 에러 토스트가 아니라 "안전장치가 작동 중입니다"
       * 전용 UI로 분기(사용자가 재시도하면 안 되는 상황임을 명확히 전달). */
    },
  });
}
```

```typescript
// packages/shared-hooks/src/useAuthStore.ts
// STATUS: SCAFFOLD-READY
import { create } from "zustand";

interface AuthState {
  token: string | null;
  user: UserResponse | null;
  setToken: (token: string) => void;
  logout: () => void;
}

/** 클라이언트 로컬 상태(토큰, 현재 사용자)만 Zustand로 관리 — 서버 데이터는
 * 절대 여기 두지 않는다(TanStack Query와 역할 분리 원칙). */
export const useAuthStore = create<AuthState>((set) => ({
  token: null,
  user: null,
  setToken: (token) => set({ token }),
  logout: () => set({ token: null, user: null }),
}));
```

---

## §17.5 핵심 화면 컴포넌트 예시 (복잡도 높은 3개만 상세, 나머지는 착수 시 동일 패턴 적용)

### 17.5.1 전략 편집기 — `ConditionBuilder.tsx`

```typescript
// STATUS: SCAFFOLD-READY
interface ConditionBuilderProps {
  value: ConditionSpec;
  onChange: (next: ConditionSpec) => void;
  availableIndicators: string[]; // useIndicatorList() 훅으로 GET /indicators 결과
}

export function ConditionBuilder({ value, onChange, availableIndicators }: ConditionBuilderProps) {
  /** FD-14.2 — 코드 작성 없이 드롭다운(지표 선택) + 숫자입력(파라미터) +
   * 연산자 선택으로 구성. 저장 시 STRATEGY_CONDITION_INVALID(400) 응답을
   * 필드 단위 에러로 매핑해서 보여준다(백엔드가 반환하는 conflicting_states를
   * 그대로 사용자에게 노출하지 않고, "이 조건 조합은 동시에 만족할 수 없습니다"
   * 같은 일반 사용자 언어로 번역 — details 필드는 개발자 콘솔에만 로깅). */
  ...
}
```

### 17.5.2 실행 제어판 — `ExecutionCard.tsx`

```typescript
// STATUS: SCAFFOLD-READY
interface ExecutionCardProps {
  execution: ExecutionMonitorResponse;
}

export function ExecutionCard({ execution }: ExecutionCardProps) {
  const startMutation = useStartExecution();
  const isBlocked = execution.status === "PAUSED" && execution.pausedBy === "SAFETY_LAYER";
  // 16번 문서 v1.3 후속 갱신으로 pausedBy 필드 추가됨(재점검 라운드에서 해결) —
  // Watchdog 자동정지와 사용자 수동정지를 이제 명확히 구분해서 표시 가능
  ...
}
```

### 17.5.3 위험등급 경고 모달 — `RiskWarningModal.tsx`

```typescript
// STATUS: SCAFFOLD-READY
interface RiskWarningModalProps {
  reason: string;          // purchaseResponse.riskWarningReason (FD-15.3)
  onConsent: () => void;   // 동의 시 구매/배포 재요청(consent 플래그 포함)
  onCancel: () => void;
}

export function RiskWarningModal({ reason, onConsent, onCancel }: RiskWarningModalProps) {
  /** 마켓플레이스 구매(ListingDetailPage), 전략 배포 승인(MyStrategiesPage),
   * ApprovalMode 변경(ApprovalSettingsPage) 3곳에서 동일 컴포넌트 재사용
   * (FD-15.3 트리거 3종과 1:1 대응) — 로직 중복 없이 컴포넌트만 재사용. */
  ...
}
```

---

## §17.6 디자인시스템 기초 (Draft — 착수 시 확정)

- 토큰: 색상/타이포/스페이싱은 착수 시 `packages/ui-web/tokens.ts`로 확정(frontend-design
  스킬 참조 대상 — 실제 코드 작성 세션에서 적용)
- Critical Risk 승인·Watchdog 경고 관련 UI는 **색상만으로 위험도를 구분하지 않는다**
  (색맹 접근성 + 4.9/8.6-B 안전장치 UI는 텍스트로도 명확해야 한다는 원칙 — "경고"
  아이콘+텍스트 병기 필수, Draft이지만 타협 대상 아님)
- 차트(포트폴리오 도넛차트, 보고서 손익추이): Recharts 제안(Draft)

---

## §17.7 apps/mobile 개요 (FD-21, 별도 상세 문서 필요)

```
apps/mobile/src/
├── screens/            # apps/web/src/routes와 1:1 대응(화면 자체는 재구성,
│                        # 비즈니스 로직은 shared-hooks 재사용)
├── navigation/          # React Navigation — §17.3 라우팅 테이블을 스택/탭으로 재구성
└── native/
    ├── biometricAuth.ts # FD-21.2 — Keychain/Keystore 연동, 서버 API 없음
    └── pushRegistration.ts # FD-21.1 — POST /devices 호출
```

`apps/mobile`은 화면 컴포넌트 자체는 웹과 다르게(RN 네이티브 컴포넌트로) 새로
작성하되, `packages/shared-hooks`(useExecutions, useAuthStore 등)와
`packages/api-client`는 그대로 import — API 호출·상태관리 로직은 웹과 정확히
동일한 코드를 쓴다. 상세 화면 설계는 웹 화면(17.1~17.5) 확정 후 별도 라운드로
진행 권장(지금 동시에 하면 웹 쪽 변경이 잦을 때 모바일도 매번 다시 그려야 함).

---

## §17.7-A 실행 명령 (신규 — "모든 문서 실제 구현가능성 검증" 라운드)

```bash
# --- 최초 1회 셋업 (모노레포 루트에서) ---
npm install                       # 또는 pnpm install(Turborepo/Nx 선택에 따라, §17.0)
cp apps/web/.env.example apps/web/.env
# VITE_API_BASE_URL=http://localhost:8000 등 설정

# --- 개발 서버 기동 ---
npm run dev --workspace=apps/web  # Vite 개발서버, 기본 http://localhost:5173

# --- 빌드 ---
npm run build --workspace=apps/web
```

- 백엔드(§16.12-A)를 먼저 `localhost:8000`에 띄운 뒤 프론트엔드를 기동해야
  API 클라이언트(§17.2)가 정상 동작한다.
- `packages/shared-types`, `packages/api-client`는 모노레포 워크스페이스로
  `apps/web`이 자동 참조 — 별도 배포/게시(publish) 불필요(Phase 1 스콥,
  17.9-A 과잉설계 방지).

## §17.8 작업 트리 매핑 (10번 문서 갱신 필요)

```
├── 22.2 packages/shared-types (16번 API 전체 TS 타입 변환)
├── 22.3 packages/api-client (16번 라우터 전체 1:1 메서드)
├── 22.4 packages/shared-hooks (도메인별 useXxx 훅)
├── 22.5 apps/web 라우팅·레이아웃 골격
├── 22.6 apps/web 화면 19개 (§17.3 순서대로, 인증→온보딩→대시보드 우선)
├── 22.7 디자인 토큰 확정 (frontend-design 스킬 적용, 22.5 이전 권장)
└── 22.8 apps/mobile 착수 — 22.6 웹 화면 안정화 이후(17.7 원칙)
```
