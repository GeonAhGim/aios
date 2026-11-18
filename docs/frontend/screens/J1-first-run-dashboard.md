# J1 화면 명세 -- 최초 실행·거래소 연결·데모 모드·대시보드

> 생성일: 2026-11-18 | 작업자: claude-local-2 | 리프: task-25727
> 관련 여정: UX_JOURNEYS.md §6 J1 "처음 로그인 — 연결 → 전략 → 첫 실행"

---

## 1. /onboarding/first-run (OnboardingFlowPage)

**역할:** 온보딩 3단계 진행 상황 표시 + 완료 시 축하 화면

**라우트 등록:** `router.tsx:89` — `protect(<OnboardingFlowPage />)`

### 1.1 데이터 소스

| Hook | API | 폴링 | 파일:줄 |
|---|---|---|---|
| `useExchangeCredentials()` | `GET /exchange-credentials` | 없음 | `useConnections.ts:42` |
| `useMyStrategies()` | `GET /my-strategies` | 없음 | `useStrategies.ts:2` |
| `usePaperDeployments()` | `GET /paper-deployments` | 없음 | `usePaperControl.ts:30` |

진행도 판정 로직은 `onboardingProgress.ts:13-54` (`deriveOnboardingProgress`)에서 처리:

- **1단계 (거래소 연결):** `credentials.data.length > 0 && !credentials.isError`
- **2단계 (전략 생성):** `strategies.data.length > 0 && !strategies.isError`
- **3단계 (페이퍼 실행):** `deployments.data.deployments.length > 0 && !deployments.isError`
- 모든 단계가 완료되면 `isComplete = true`

### 1.2 화면 레이아웃

```
+--------------------------------------------------+
| 시작하기                                         |
+--------------------------------------------------+
| [1] 거래소 연결   [완료 배지 or 진행 중]         |
|     읽기 전용 API 키로 거래소 계정을 연결하세요.  |
|     [거래소 연결하기 -> /exchanges]              |
+--------------------------------------------------+
| [2] 전략 생성     [완료 배지 or 진행 중]         |
|     전략 편집기에서 조건을 구성...               |
|     [전략 만들기 -> /strategy-builder]           |
+--------------------------------------------------+
| [3] 페이퍼 실행   [완료 배지 or 진행 중]         |
|     실계좌 자금 없이 모의 계좌로...              |
|     [페이퍼 실행하기 -> /executions]            |
+--------------------------------------------------+
| (모든 단계 완료 시)                              |
| 첫 실행을 완주했습니다                           |
| 거래소 연결, 전략 생성, 페이퍼 실행까지 모두...   |
| [대시보드로 이동 -> /dashboard]                  |
+--------------------------------------------------+
```

### 1.3 상태별 동작

| 상태 | 동작 |
|---|---|
| 로딩 중 (`isLoading=true`) | CTA 링크 대신 로딩 표시 |
| API 오류 (`isError=true`) | 해당 단계는 완료로 인정하지 않음 (fail-closed, `test.tsx:84-90`) |
| 캐시된 데이터 존재 | `isError=false`일 때만 완료 인정 |

### 1.4 갭 표

| 갭 ID | 심각도 | 내용 | 근거 |
|---|---|---|---|
| G-11 | 중 | `/exchanges` 등 하위 화면에서 온보딩 진행률 배지가 소실됨. 진행률 보려면 `/onboarding/first-run`으로 직접 이동 필요 | `OnboardingProgressWidget.tsx:1-41` — 재사용 화면에 배치된 하위 위젯 |

---

## 2. /onboarding/connect (ConnectionWizardPage)

**역할:** 거래소 연결 3단계 마법사

**라우트 등록:** `router.tsx:90-97` — `FeatureFlagGate flag="onboarding_connection_wizard"`

### 2.1 데이터 소스

| Hook | API | 폴링 | 파일:줄 |
|---|---|---|---|
| `useBeginConnection()` | `POST /connections` | 없음 | `useConnections.ts:17` |
| `useRegisterExchangeCredential()` | `POST /exchange-credentials` | 없음 | `exchange.ts:50-55` |
| `useRevokeExchangeCredential()` | `DELETE /exchange-credentials/:exchange` | 없음 | `exchange.ts:61-63` |

### 2.2 3단계 흐름

#### 2단계 1: 공급자 선택

```
+--------------------------------------------------+
| 1. 공급자 선택                                   |
+--------------------------------------------------+
| 공급자 코드 [드롭다운]                           |
| 계정 키(opaque_account_ref) [입력 필드]          |
|   힌트: 키는 서버 KeyRing에만 저장...            |
| [요청 권한(읽기 전용만 가능)]                    |
|     [ ] 읽기 전용 계정 정보                      |
|     [ ] 읽기 전용 잔고                           |
|     [ ] 읽기 전용 주문 내역                      |
|          [다음]                                  |
+--------------------------------------------------+
```

- **입력 검증:** `apiKey`가 비어 있으면 `API_KEY_REQUIRED` 에러
- **권한 요청:** `CapabilityScope[]` (`READ_BALANCE`, `READ_POSITION`, `READ_ACTIVITY`)
- **키 보안:** `maskKey()`로 마스킹 표시 (catalog.ko.ts:324 "키는 서버 KeyRing에만 저장")
- **서버 검증:** 서버가 실거래소 인증으로 거부하면 400 반환 → 연결 실패 안내

#### 2단계 2: 권한 점검

```
+--------------------------------------------------+
| 2. 권한 점검                                     |
+--------------------------------------------------+
| 읽기 전용 권한만 요청됨을 확인했습니다.          |
| [확인 체크박스] 읽기 전용 권한만 요청됨을...     |
|          [이전]          [다음]                  |
+--------------------------------------------------+
```

- **불변식:** 주문·출금 권한은 요청되지 않음 (catalog.ko.ts:330)
- **체크박스:** 확인 전에는 "다음" 비활성화

#### 2단계 3: 검토 및 연결

```
+--------------------------------------------------+
| 3. 검토 및 연결                                  |
+--------------------------------------------------+
| 공급자: bitget                                   |
| 계정 키: ****...****                             |
| 권한: 읽기 전용 계정 정보, 읽기 전용 잔고...     |
|          [이전]          [연결하기]              |
+--------------------------------------------------+
```

- **성공 시:** `ConnectionWizardSuccess` 모달 — "연결 요청을 보냈습니다. 설정 > 연결에서 상태를 확인하세요."
- **실패 시:** `ConnectionWizardError` 모달 — "연결에 실패했습니다. 키를 확인하고 다시 시도하세요."

### 2.3 API 스키마

**요청 (`BeginConnectionRequest`):**
```json
{
  "providerCode": "bitget",
  "opaqueAccountRef": "발급받은 키",
  "requestedCapabilityProfile": ["READ_BALANCE", "READ_POSITION"]
}
```

**응답 (`AccountConnectionView`):**
```json
{
  "id": "ulid-string",
  "providerCode": "bitget",
  "maskedAccountLabel": "계정 1234",
  "state": "PENDING_CONSENT",
  "capabilityProfile": ["READ_BALANCE", "READ_POSITION"],
  "revision": 1,
  "createdAt": "2026-11-18T00:00:00Z",
  "scopeVerified": false,
  "schemaVersion": "v1"
}
```

### 2.4 갭 표

| 갭 ID | 심각도 | 내용 | 근거 |
|---|---|---|---|
| G-2 | 중 | 가짜 API 키 입력 시 서버가 실거래소 인증으로 400 반환 — 화면은 "연결에 실패했습니다" 일반 메시지만 표시. 구체적 원인(잘못된 키/권한 부족/서버 오류) 구분 불가 | `ConnectionWizardPage.tsx:160-165` — `onError`가 단일 alert |
| G-3 | 경 | 연결 요청 후 `state=PENDING_CONSENT`일 때 사용자에게 "어디서 상태를 확인하는지"가 모호. "설정 > 연결"이 어디인지 명시 없음 | `ConnectionWizardPage.tsx:169` — "설정 > 연결에서 상태를 확인하세요" |

---

## 3. /onboarding/demo (DemoModePage)

**역role:** 데모 모드 종목 목록 — 서버 없이 도는 오프라인 시뮬레이션

**라우트 등록:** `router.tsx:98-105` — `FeatureFlagGate flag="onboarding_demo_mode"`

### 3.1 데이터 소스

| 소스 | 타입 | 파일:줄 |
|---|---|---|
| 샘플 종목 목록 | `demoDataset.ts:10-60` (결정론적 상수) | `DemoModePage.tsx:37` |
| 데모 데이터셋 | `DemoDataset` 인터페이스 | `demoDataset.ts:10-60` |

### 3.2 화면 레이아웃

```
+--------------------------------------------------+
| 데모 모드                                        |
+--------------------------------------------------+
| 고정 샘플 데이터(1년치 1분봉, 3종목)로 실제...   |
+--------------------------------------------------+
| 샘플 종목                                        |
+--------------------------------------------------+
| [ETH/USDT] 1년치 1분봉 365일 ...  [데모 시작]   |
| [BTC/USDT] 1년치 1분봉 365일 ...  [데모 시작]   |
| [KRW-ETH] 1년치 1분봉 365일 ...   [데모 시작]   |
+--------------------------------------------------+
| ⚠ 이 데모는 서버 없이 로컬에서 동작하는          |
|   시뮬레이션입니다. 실제 거래와 연결되지 않습니다. |
+--------------------------------------------------+
```

### 3.3 데모 데이터셋 (demoDataset.ts)

| 종목 | 심볼 | 데이터 타입 | 기간 | 캔들 수 |
|---|---|---|---|---|
| ETH/USDT | `demo:ETHUSDT` | `CRYPTO` | 1년 M1 | 525,600 |
| BTC/USDT | `demo:BTCUSDT` | `CRYPTO` | 1년 M1 | 525,600 |
| KRW-ETH | `demo:KRWETH` | `KR_EQUITY` | 1년 M1 | 525,600 |

**데이터 생성:** `generateDeterministicCandles()` — 시드 기반 결정론적 랜덤 (seed: `instrumentId`)

### 3.4 갭 표

| 갭 ID | 심각도 | 내용 | 근거 |
|---|---|---|---|
| G-8 | 중 | 데모 모드 고지 — "서버 없이 로컬에서 동작하는 시뮬레이션"이라는 안내가 하단 작은 텍스트로만 존재. 사용자가 데모를 실제 거래로 오인할 가능성 있음 | `DemoModePage.tsx:130-144` — Footer에 작은 글씨 |
| G-9 | 경 | 525,600캔들/종목 × 3종목 = 1,576,800캔들을 메모리에 로드. 첫 렌더 시 성능 저하 가능성 | `demoDataset.ts:48-52` — 3종목 동시 생성 |

---

## 4. /onboarding/demo/:instrumentId (DemoChartPage)

**역할:** 데모 차트 + 백테스트 실행

**라우트 등록:** `router.tsx:106-113` — `FeatureFlagGate flag="onboarding_demo_mode"`

### 4.1 화면 레이아웃

```
+--------------------------------------------------+
| [<- 데모 종목 목록으로]    데모 차트             |
+--------------------------------------------------+
| 차트 영역 (캔들스틱 + 지표)                       |
+--------------------------------------------------+
| [지금 백테스트 실행]                             |
+--------------------------------------------------+
| (백테스트 결과 모달)                              |
| 백테스트 요약                                    |
| 최종 자산: $XXX                                  |
| 거래 횟수: XX                                    |
| 최대 낙폭: X.XX%                                 |
+--------------------------------------------------+
```

### 4.2 데이터 흐름

1. **차트 렌더:** `demoDataset.ts`에서 생성된 결정론적 캔들을 차트 엔진에 전달
2. **백테스트 실행:** `demoBacktest.ts:14-78` — 고정 전략(단기 이동평균 크로스오버)으로 시뮬레이션
3. **결과 표시:** `DemoBacktestResult` 객체를 모달로 표시

### 4.3 백테스트 결과 필드 (demoBacktest.ts)

```typescript
interface DemoBacktestResult {
  finalEquity: number;
  totalReturn: number;
  maxDrawdown: number;
  sharpeRatio: number;
  winRate: number;
  tradeCount: number;
  trades: Trade[];
}
```

### 4.4 갭 표

| 갭 ID | 심각도 | 내용 | 근거 |
|---|---|---|---|
| G-10 | 경 | 미확인: 차트 렌더링이 어떤 차트 엔진 패키지를 사용하는지 확인 못 함. `DemoChartPage.tsx`에서 차트 컴포넌트 import 경로 미확인 | `DemoChartPage.tsx:1-20` |

---

## 5. /exchanges (ExchangeManagementPage)

**역할:** 거래소 연결 관리 — 등록·조회·해제·잔고·역할

**라우트 등록:** `router.tsx:115` — `protect(<ExchangeManagementPage />)`

### 5.1 데이터 소스

| Hook | API | 폴링 | 파일:줄 |
|---|---|---|---|
| `useExchangeCredentials()` | `GET /exchange-credentials` | 없음 | `useConnections.ts:42` |
| `useRegisterExchangeCredential()` | `POST /exchange-credentials` | 없음 | `exchange.ts:50-55` |
| `useRevokeExchangeCredential()` | `DELETE /exchange-credentials/:exchange` | 없음 | `exchange.ts:61-63` |
| `useExchangeBalance()` | `GET /exchange-credentials/:exchange/balance` | 없음 | `exchange.ts:65-68` |
| `useExchangeCapabilities()` | `GET /exchange-credentials/:exchange/capabilities` | 없음 | `exchange.ts:70-73` |

### 5.2 화면 레이아웃

```
+--------------------------------------------------+
| 거래소                                           |
+--------------------------------------------------+
| [거래소 연결하기]                                 |
+--------------------------------------------------+
| 거래소 목록                                       |
+--------------------------------------------------+
| bitget           [연결됨]  [잔고] [역할] [해제]   |
|                  잔고: 1,000.00 USDT             |
+--------------------------------------------------+
| (빈 상태)                                         |
| 연결된 거래소가 없습니다.                         |
| [온보딩 시작하기 -> /onboarding/first-run]       |
+--------------------------------------------------+
```

### 5.3 CredentialResponse 필드

| 필드 | 타입 | 설명 |
|---|---|---|
| `id` | `number` | 자격 증명 ID |
| `exchange` | `string` | 거래소 코드 |
| `isActive` | `boolean` | 활성 여부 |
| `linkedAt` | `string` | 연결 시각 (UTC ISO) |
| `withdrawalPermissionWarning` | `string\|null` | 출금 권한 경고 |

### 5.4 갭 표

| 갭 ID | 심각도 | 내용 | 근거 |
|---|---|---|---|
| G-4 | 중 | 가짜 API 키 입력 시 서버가 실거래소 인증으로 400 반환 — 화면은 "연결에 실패했습니다" 일반 메시지만 표시 | `ExchangeManagementPage.tsx:119-125` |
| G-5 | 경 | `useExchangeBalance`가 `refetchInterval` 없이 단회 조회. 잔고 정보가 stale될 수 있음 | `useExchanges.ts:34-38` |

---

## 6. /dashboard (DashboardPage)

**역할:** 메인 대시보드 — 포트폴리오·실행·알림 요약

**라우트 등록:** `router.tsx:114` — `protect(<DashboardPage />)`

### 6.1 데이터 소스

| Hook | API | 폴링 | 파일:줄 |
|---|---|---|---|
| `useRiskProfile()` | `GET /risk-profile` | 없음 | `useSuitability.ts:6` |
| `usePortfolio()` | `GET /portfolio` | 없음 | `usePortfolio.ts:12` |
| `useExecutions()` | `GET /executions` | **5초** | `usePortfolio.ts:37-44` |
| `useNotificationHistory()` | `GET /notifications` | 없음 | `useAccountSettings.ts:45` |

### 6.2 화면 레이아웃

```
+--------------------------------------------------+
| 대시보드              [위험 프로필 배지]          |
+--------------------------------------------------+
| [작업 상태 바: 실행 중/일시정지/전체 정지]        |
+--------------------------------------------------+
| 포트폴리오                    [데이터 신선도]     |
+--------------------------------------------------+
| 총 포트폴리오 가치: $XX,XXX                       |
| 미할당 현금: $X,XXX                               |
| 할당된 자산: X개                                   |
| [자산 배분 차트]                                  |
+--------------------------------------------------+
| 실행 중 전략                                       |
+--------------------------------------------------+
| 전략 A    $1,000    RUNNING    [일시정지]         |
| 전략 B    $500      PAUSED     [재개]             |
+--------------------------------------------------+
| (빈 상태)                                         |
| 실행 중인 전략이 없습니다.                        |
| [온보딩 시작하기]                                 |
+--------------------------------------------------+
| 최근 알림                                         |
+--------------------------------------------------+
| [이벤트 타입]    [상태 배지]                      |
+--------------------------------------------------+
```

### 6.3 포트폴리오 필드 (portfolio)

| 필드 | 타입 | 설명 |
|---|---|---|
| `totalPortfolioValue` | `string` | 총 포트폴리오 가치 |
| `unallocatedCash` | `string` | 미할당 현금 |
| `allocations` | `Allocation[]` | 자산 할당 목록 |

### 6.4 실행 필드 (execution)

| 필드 | 타입 | 설명 |
|---|---|---|
| `id` | `number` | 실행 ID |
| `status` | `string` | 상태 (RUNNING/PAUSED/etc.) |
| `strategyId` | `string` | 전략 ID |
| `capital` | `string` | 자본 |
| `pnl` | `string` | 손익 |

### 6.5 알림 필드 (notification)

| 필드 | 타입 | 설명 |
|---|---|---|
| `eventType` | `string` | 이벤트 타입 |
| `status` | `string` | 상태 |

### 6.6 데이터 신선도

- `DataFreshness` 컴포넌트(`DataFreshness.tsx:1-26`)가 `asOf` prop으로 신선도 표시
- **주의:** `GET /portfolio`는 아직 `ApiResponse` 봉투 미적용 (`PLT-19` 미정) — `meta.as_of` 없음
- 따라서 `asOf={null}`으로 고정 → "확인 불가" 표시 (DashboardPage.tsx:169-174)

### 6.7 긴급 정지 패널

- 실행 중 전략 전체 일괄 정지 버튼 (`EmergencyStopPanel`)
- 개별 실행 일시정지/재개 버튼 포함
- `usePauseExecution` / `useStartExecution` 훅 사용

### 6.8 갭 표

| 갭 ID | 심각도 | 내용 | 근거 |
|---|---|---|---|
| G-6 | 중 | 포트폴리오 조회가 5xx 등으로 실패하면 카드 섹션 통째로 사라지고 오류 표시 안 함 (무음 실패). `isError` 표면화 이전의 문제. 현재는 `ErrorMessage`로 개선됨 | `DashboardPage.tsx:176-185` (F-6 주석) |
| G-7 | 중 | `useExecutions()`의 5초 폴링이 실패하면 낡은 데이터를 계속 표시. 폴링 실패 시 "낡은 데이터 표시" 안내 없음 | `usePortfolio.ts:39-43` — `refetchInterval: 5000`만 설정, stale 시간 제어 없음 |
| G-12 | 경 | 포트폴리오 데이터 신선도 표시가 `asOf={null}`으로 고정되어 항상 "확인 불가" — PLT-19 이후 자동 개선되지만 현재는 의미 없음 | `DashboardPage.tsx:174` |

---

## 7. 온보딩 진행률 위젯 (OnboardingProgressWidget)

**역할:** 하위 화면 상단에 "N/3단계 진행 중" 배지 + 체크리스트 링크

**재사용 위치:** `OnboardingFlowPage.tsx:10-12` (본인), `OnboardingProgressWidget.tsx:1-41`

### 7.1 화면

```
+--------------------------------------------------+
| [온보딩 1/3단계 진행 중] 온보딩 체크리스트 보기  |
+--------------------------------------------------+
```

- 완료 시 (`isComplete=true`) 위젯 숨김
- 로딩 중일 때 (`isLoading=true`) 위젯 숨김

### 7.2 갭 표

| 갭 ID | 심각도 | 내용 | 근거 |
|---|---|---|---|
| G-11 | 중 | `/exchanges` 등 하위 화면에서 온보딩 진행률 배지가 소실 | `OnboardingProgressWidget.tsx` — 하위 화면에 수동 배치 필요 |

---

## 8. 공통 컴포넌트 참조

| 컴포넌트 | 패키지 | 파일 |
|---|---|---|
| `Card`, `CardTitle`, `Stat` | `@aios/ui-web` | `Card.tsx:1-30` |
| `Badge` | `@aios/ui-web` | `Badge.tsx:18` |
| `StatusBadge` | `@aios/ui-web` | `StatusBadge.tsx:60` |
| `EmptyState`, `LoadingState`, `PageHeader` | `@aios/ui-web` | `Feedback.tsx:22-40` |
| `AppShell` | `apps/web` | `AppShell.tsx` |
| `OperationStatusBar` | `apps/web` | `OperationStatusBar.tsx:61` |
| `DataFreshness` | `apps/web` | `DataFreshness.tsx` |
| `ErrorMessage` | `apps/web` | `ErrorMessage.tsx` |
| `Button` | `@aios/ui-web` | — |

---

## 9. i18n 키 (한국어)

| 네임스페이스 | 키 | 한국어 |
|---|---|---|
| `onboarding.pageTitle` | — | 시작하기 |
| `onboarding.emptyStateCta` | — | 온보딩 시작하기 |
| `onboarding.stepDone` | — | 완료 |
| `onboarding.stepUpcoming` | — | 예정 |
| `onboarding.completeTitle` | — | 첫 실행을 완주했습니다 |
| `onboarding.completeDescription` | — | 거래소 연결, 전략 생성, 페이퍼 실행까지 모두 마쳤습니다. |
| `onboarding.goToDashboard` | — | 대시보드로 이동 |
| `onboarding.progressWidget` | — | 온보딩 {{completed}}/{{total}}단계 진행 중 |
| `onboarding.steps.connectExchange.title` | — | 거래소 연결 |
| `onboarding.steps.connectExchange.description` | — | 읽기 전용 API 키로 거래소 계정을 연결하세요. |
| `onboarding.steps.connectExchange.cta` | — | 거래소 연결하기 |
| `onboarding.steps.createStrategy.title` | — | 전략 생성 |
| `onboarding.steps.createStrategy.description` | — | 전략 편집기에서 조건을 구성하고 첫 전략을 만드세요. |
| `onboarding.steps.createStrategy.cta` | — | 전략 만들기 |
| `onboarding.steps.runPaper.title` | — | 페이퍼 실행 |
| `onboarding.steps.runPaper.description` | — | 실계좌 자금 없이 모의 계좌로 전략을 실행해보세요. |
| `onboarding.steps.runPaper.cta` | — | 페이퍼 실행하기 |
| `connectWizard.pageTitle` | — | 연결 마법사 |
| `demoMode.pageTitle` | — | 데모 모드 |
| `demoMode.description` | — | 고정 샘플 데이터(1년치 1분봉, 3종목)로 실제 연결 없이 차트와 백테스트를 체험합니다. |

---

## 10. 전체 갭 표

| ID | 심각도 | 화면 | 내용 | 근거 |
|---|---|---|---|---|
| G-2 | 중 | connect | 가짜 키 → 서버 400 → 일반 실패 메시지만. 구체적 원인 구분 불가 | `ConnectionWizardPage.tsx:160-165` |
| G-3 | 경 | connect | PENDING_CONSENT 시 "설정 > 연결" 위치 모호 | `ConnectionWizardPage.tsx:169` |
| G-4 | 중 | exchanges | connect와 동일 — 가짜 키 400 → 일반 실패 메시지 | `ExchangeManagementPage.tsx:119-125` |
| G-5 | 경 | exchanges | `useExchangeBalance` stale 가능성 (폴링 없음) | `useExchanges.ts:34-38` |
| G-6 | 중 | dashboard | 포트폴리오 5xx 시 무음 실패 (현재 `ErrorMessage`로 개선됨) | `DashboardPage.tsx:176-185` |
| G-7 | 중 | dashboard | 5초 폴링 실패 시 낡은 데이터 표시 안내 없음 | `usePortfolio.ts:39-43` |
| G-8 | 중 | demo | 데모 모드 고지가 작은 텍스트로만 존재 — 실제 거래 오인 가능성 | `DemoModePage.tsx:130-144` |
| G-9 | 경 | demo | 1,576,800캔들 메모리 로드 성능 | `demoDataset.ts:48-52` |
| G-10 | 경 | demo | 차트 엔진 미확인 | `DemoChartPage.tsx:1-20` |
| G-11 | 중 | 전 화면 | 하위 화면에서 온보딩 진행률 배지 소실 | `OnboardingProgressWidget.tsx` |
| G-12 | 경 | dashboard | 포트폴리오 신선도 `asOf={null}` 고정 | `DashboardPage.tsx:174` |
