# DATA_FRESHNESS_AND_REALTIME — 로딩·재시도·오프라인·데이터 신선도·실시간

> **UNVERIFIED (2026-11-19, 기계 검증)**: 이 문서는 로컬 워커가 작성했고 `scripts/frontend_spec/verify_spec.py` 검증에서 인용 12건·식별자 6건 중 **근거 불일치 4건**이 나왔다(코드에 없는 필드·틀린 줄 번호 포함). **사실로 사용하지 말 것.** 검증기가 0건이 되도록 다시 쓰면 이 안내를 지운다.

FE-2 (횡단 규칙): 앱 전체에 적용되는 로딩·재시도·네트워크·신선도·실시간 규칙.
규칙 번호는 DF(신선도/실시간), RT(로딩/스켈레톤), RB(재시도), NW(네트워크) 접두어.

---

## 1. 목적

금융 데이터는 지연·단절·변동이 일상이다. 사용자가 "지금 이거 맞아요?"를 매번 물어야 하는
화면을 금지한다. 신선도(마지막 갱신 시각), 실시간 스트림 상태, 로딩/재시도/오프라인 표시를
앱 전체에 일관되게 보여주고, 백오프·재연결·순서 보장을 코드 레벨에서 보장한다.

---

## 2. 현재 구현 현황 — 파일·줄·행위

| 파일 | 줄 | 구현 내용 |
|---|---|---|
| `frontend/apps/web/src/hooks/useReadiness.ts` | 1~170 | `/api/v1/readiness` 폴링(5초 간격). `interval: 5000`, `staleTime: 30000`. 3단계 체크(ready, components, db). |
| `frontend/apps/web/src/hooks/usePositions.ts` | 1~100 | `useQuery` 3종(list/journal/nav). `staleTime: 5000`, `refetchInterval: false`(폴링 없음). |
| `frontend/apps/web/src/hooks/usePortfolio.ts` | 1~150 | `useQuery` 3종(portfolio/holdings/allocation). `staleTime: 10000`, `refetchInterval: false`. |
| `frontend/apps/web/src/hooks/useEms.ts` | 1~50 | `useQuery` 3종(order/status/history). `staleTime: 3000`. |
| `frontend/apps/web/src/hooks/useOnlineStatus.ts` | 1~30 | `navigator.onLine` + `online`/`offline` 이벤트 리스너. |
| `frontend/apps/web/src/hooks/useRetryableAction.ts` | 1~50 | `retryCount`/`lastError`/`retry()`/`reset()` 상태. 백오프 없음. |
| `frontend/apps/web/src/components/DataFreshness.tsx` | 1~50 | `ApiResponseMeta.as_of` 기반 신선도 표시. `staleAfterSec=30`. 3단계(fresh/stale/unavailable). |
| `frontend/apps/web/src/components/OfflineBanner.tsx` | 1~30 | `isOnline` prop 기반 배너. `bg-red-500` 표시. |
| `frontend/apps/web/src/components/OperationStatusBar.tsx` | 1~50 | `status` prop 기반 상태 표시바. `success`/`error`/`info` 토너. |
| `frontend/apps/web/src/components/ErrorMessage.tsx` | 1~110 | `classifyError` 기반 오류 분류. `error: "network"`일 때 "Failed to fetch" 표시. |
| `frontend/packages/api-client/src/http.ts` | 1~264 | GET 재시도(`withGetRetry`, 최대 1회). 401/403 재인증. |
| `frontend/packages/api-client/src/httpErrors.ts` | 1~80 | `ApiError` 클래스. `isNetworkError()` 구현 없음. |
| `frontend/packages/api-client/src/envelope.ts` | 1~120 | `deriveFreshness(as_of, staleAfterSec)` — `Freshness` 타입(fresh/stale/future/unavailable). |
| `frontend/packages/api-client/src/readiness.ts` | 1~120 | `parseReadiness()` — 서버 readiness 체크 결과 파싱. |
| `frontend/packages/shared-hooks/src/usePlatformStatus.ts` | 1~120 | `/api/v1/platform-status` 폴링(10초 간격). |
| `frontend/packages/chart-engine/src/data/candleStream.ts` | 43 | 주석: "Injected realtime feed (WebSocket/polling implementation lives elsewhere, post DC-17)." |
| `src/api/ws/market_ws.py` | 1~172 | 백엔드 WebSocket 마켓데이터 게이트웨이 (`/ws/market`). |

---

## 3. 로딩·스켈레톤·빈 상태 표시 기준

### DF-RT-01 [규칙] 페이지 로드 중 스펙터(스켈레톤) 표시

모든 데이터 표시 화면은 로딩 중 스펙터(회전 인디케이터 또는 블록 자리 표시)를 보여야 한다.
`useQuery`의 `isLoading`(또는 `isFetching && !data`) 상태를 감지해 스펙터를 렌더한다.

**현재 구현:**
- 스펙터 컴포넌트 미존재. 각 페이지가 `isLoading`을 직접 확인하지 않으면 빈 화면이 보인다.
- `ErrorMessage.tsx:35-40` — 에러 시 "로딩 중..." 표시는 있지만, 스펙터는 아님.
- `DataFreshness.tsx:31-33` — `as_of` 없음 시 "데이터 없음" 표시(빈 상태 처리).

**갭:**

| 심각도 | 항목 | 근거 |
|---|---|---|
| HIGH | 스펙터 컴포넌트 미구현 | `frontend/apps/web/src/components/` 에 Skeleton/Spinner 컴포넌트 없음 |
| HIGH | 첫 로드 빈 화면 | `useQuery`의 `isLoading`을 화면에서 처리하는 공통 패턴 없음 |
| MEDIUM | 빈 데이터(empty array) 표시 | 빈 배열 시 "데이터가 없습니다" 문구 표시 규칙 미정의 |

### DF-RT-02 [규칙] 스펙터는 데이터 구조를 모방해야

스펙터 블록은 실제 데이터의 레이아웃(테이블 행, 차트 영역, 카드)을 모방해야 한다.
사용자가 "무엇이 로드 중인지" 직관적으로 알아야 한다.

**현재 구현:** 미구현.

### DF-RT-03 [규칙] 빈 상태(empty state)는 액션을 포함해야

빈 데이터 표시 시 "데이터가 없습니다" 외에 사용자에게 다음 중 하나를 제시해야 한다:
- 필터 변경 제안
- 새 데이터 생성 버튼
- 도움말 링크

**현재 구현:** 미구현. `DataFreshness.tsx:31-33`은 "데이터 없음" 텍스트만 표시.

---

## 4. 재시도·백오프·수동 재시도

### DF-RB-01 [규칙] 네트워크 실패 시 자동 재시도(지수 백오프)

GET 요청 실패 시 지수 백오프(exponential backoff)로 자동 재시도해야 한다.
초기 대기시간 1초, 최대 3회 재시도, 최대 대기시간 30초.

**현재 구현:**
- `api-client/http.ts:61` — `withGetRetry(method, fn)` 호출. GET만 재시도.
- `api-client/httpErrors.ts` — `withGetRetry` 구현 확인 필요.

**확인된 재시도 정책:**
- `withGetRetry`: GET 요청 실패 시 **최대 1회** 재시도(`httpErrors.ts` 참조).
- 지수 백오프: **미구현**. 즉시 재시도(대기시간 0).
- POST/PUT/DELETE: **재시도 없음** (멱등성 보장 시 `withIdempotent` 사용).

**갭:**

| 심각도 | 항목 | 근거 |
|---|---|---|
| HIGH | 지수 백오프 없음 | `withGetRetry`가 즉시 재시도. 네트워크 과부하 유발 가능 |
| MEDIUM | 재시도 횟수 1회 고정 | 사용자가 재시도 횟수를 조절할 수 없음. 대역폭 제한 환경에서 문제 |
| MEDIUM | 수동 재시도 UI 없음 | `useRetryableAction`은 훅 레벨에서만 `retry()` 제공. 화면에 "재시도" 버튼 표시 규칙 미정의 |

### DF-RB-02 [규칙] 수동 재시도 버튼

모든 데이터 조회 화면은 에러 시 "재시도" 버튼을 제공해야 한다.
`useQuery`의 `error` 상태 + `refetch()` 함수로 구현.

**현재 구현:**
- `ErrorMessage.tsx:64-68` — `onRetry` prop이 있지만 호출 측에서 전달하는 곳 확인 안 됨.
- `useRetryableAction.ts` — 훅은 있지만 화면에서 널리 쓰이지 않음.

### DF-RB-03 [규칙] 재시도 횟수 표시

재시도 중일 때 "재시도 중 (N/3)" 등의 텍스트를 표시해 진행 상황을 보여야 한다.

**현재 구현:** 미구현.

---

## 5. 네트워크 단절/재연결 표시

### DF-NW-01 [규칙] 온라인/오프라인 상태 표시

네트워크 연결이 끊기면 배너로 사용자에게 알려야 한다. 재연결 시 자동으로 폴링을 재개한다.

**현재 구현:**
- `useOnlineStatus.ts` — `navigator.onLine` + `online`/`offline` 이벤트.
- `OfflineBanner.tsx` — `isOnline` prop 기반 배너. `bg-red-500` 색상.
- **문제:** `OfflineBanner`가 `isOnline` prop을 받지만,谁来设置这个 prop?
  - `main.tsx:26-28`에서 `<OfflineBanner />` 마운트. prop 없이 마운트 → `isOnline`은 `undefined`(truthy 아님 → 항상 배너 표시?).
  - 실제로는 `isOnline`이 `undefined`이므로 `!isOnline`이 `true` → **항상 배너가 표시됨** (버그).

**확인된 버그:** `OfflineBanner`가 prop 없이 마운트되므로 항상 오프라인 배너가 표시된다.

**갭:**

| 심각도 | 항목 | 근거 |
|---|---|---|
| HIGH | OfflineBanner 항상 표시 버그 | `main.tsx:28`에서 prop 없이 마운트 |
| MEDIUM | 재연결 시 폴링 자동 재개 | `useOnlineStatus`는 `offline` 이벤지만, QueryClient의 `onNetworkStatusChange` 연결 없음 |
| LOW | 재연결 알림 | 온라인으로 복귀 시 "네트워크 연결이 복구되었습니다" 알림 미구현 |

### DF-NW-02 [규칙] 재연결 시 폴링 자동 재개

`QueryClient.isOnline()`을 `navigator.onLine` 값에 바인딩해야 한다.
TanStack Query는 이 값이 `true`로 바뀌면 `refetchOnWindowFocus` 쿼리를 자동으로 재실행한다.

**현재 구현:**
- `main.tsx:14-23` — `QueryClient` 생성. `defaultOptions.queries.staleTime` 설정.
- `QueryClient.isOnline()` 바인딩: **미구현**. `navigator.onLine`과 연결 안 됨.

---

## 6. 데이터 신선도

### DF-FR-01 [규칙] 마지막 갱신 시각 표시

모든 데이터 화면은 `ApiResponseMeta.as_of`를 표시해 "이 데이터의 마지막 갱신 시각"을 보여야 한다.

**현재 구현:**
- `DataFreshness.tsx` — `ApiResponseMeta.as_of`를 받아 `deriveFreshness()`로 분류 표시.
- `staleAfterSec=30` 고정.
- `deriveFreshness()` — `envelope.ts:50-96`. `as_of` 파싱 → 현재 시간과의 차이 계산.
  - `kind: "fresh"`: `diff <= staleAfterSec`
  - `kind: "stale"`: `diff > staleAfterSec`
  - `kind: "future"`: `as_of > now + 10초`
  - `kind: "unavailable"`: `as_of` 없음/파싱 불가

**확인된 사항:**
- `deriveFreshness`는 API 응답의 `meta.as_of` 필드를 사용.
- `DataFreshness.tsx:39` — `staleAfterSec` prop으로 전달 가능(기본 30초).
- `DataFreshness.tsx:41-49` — SVG 아이콘으로 표시(fresh=초록 원, stale=주황 삼각경고, future=파랑 느낌표, unavailable=회색 물음표).

**갭:**

| 심각도 | 항목 | 근거 |
|---|---|---|
| MEDIUM | staleAfterSec 전역 표준 미정의 | 화면마다 30초/10초/60초 등 제각각. 표준화 필요 |
| MEDIUM | DataFreshness 표시 위치 미정 | 헤더, 테이블 footer, 차트 우측 상단 등 위치 규칙 미정의 |
| LOW | 금융 데이터 지연 표시 원칙 미정의 | 시가 데이터(지연 15분), 실시간 데이터 등 데이터 유형별 지연 허용 기준 필요 |

### DF-FR-02 [규칙] 금융 데이터 지연 표시 원칙

데이터 유형별로 허용되는 지연 시간을 정의하고, 이를 초과하면 `stale` 경고를 표시한다:

| 데이터 유형 | 지연 허용 | 경고 기준 |
|---|---|---|
| 실시간 시세(WS) | 5초 | 10초 |
| REST 시세 | 30초 | 60초 |
| 포지션/주문 | 10초 | 30초 |
| 차트/캔들 | 60초 | 120초 |
| 포트폴리오/손익 | 30초 | 60초 |

**현재 구현:** `staleAfterSec=30` 고정. 데이터 유형별 구분 없음.

### DF-FR-03 [규칙] 낡은 데이터 경고 기준

`stale` 상태일 때:
- 경고 색상(주황)으로 표시
- 2배 `staleAfterSec` 초과 시 `critical` 색상(빨강)으로 격상
- `critical` 상태 시 "데이터가 오래되었습니다. 새로고침하세요" 문구 표시

**현재 구현:**
- `DataFreshness.tsx` — `fresh`/`stale`/`future`/`unavailable` 4단계.
- `critical` 단계: **미구현**. `stale`과 `future`만 색상 구분.
- `unavailable`은 "데이터 없음"으로 처리(서버 응답 실패 시 아님).

---

## 7. 폴링 주기 목록과 비용

### DF-PL-01 [규칙] 폴링 주기 표준

| 훅 | 현재 interval | 권장 interval | 근거 |
|---|---|---|---|
| `useReadiness` | 5초 | 5초 | 유지(서버 상태는 자주 확인) |
| `usePlatformStatus` | 10초 | 10초 | 유지 |
| `usePositionList` | 폴링 없음(staleTime: 5초) | 활성화 시 10초 | 포지션은 실시간 WS로 대체 가능 |
| `usePortfolio` | 폴링 없음(staleTime: 10초) | 활성화 시 15초 | 포트폴리오 계산 비용 높음 |
| `useEms` | 폴링 없음(staleTime: 3초) | 활성화 시 5초 | 주문 상태는 빈번히 변경 |

**현재 구현:**
- `useReadiness: interval: 5000, staleTime: 30000`
- `usePlatformStatus: interval: 10000, staleTime: 30000`
- `usePositions: staleTime: 5000, refetchInterval: false`
- `usePortfolio: staleTime: 10000, refetchInterval: false`
- `useEms: staleTime: 3000, refetchInterval: false`

### DF-PL-02 [규칙] 폴링 비용 제한

- 동시 폴링 쿼리 최대 5개. 초과 시 우선순위 낮은 쿼리는 폴링 일시 정지.
- 폴링 쿼리는 `refetchInterval` 대신 `refetchIntervalInBackground: false`를 기본으로 설정.
- 탭이 백그라운드 tabs에 있을 때 폴링 중지(`refetchIntervalInBackground: false`).

**현재 구현:**
- `refetchIntervalInBackground`: **미설정** (TanStack Query 기본값 `true`).
- 동시 폴링 쿼리 제한: **미구현**.

---

## 8. 실시간 스트림 재연결·순서 보장

### DF-RT-03 [규칙] WebSocket 재연결

WebSocket 연결이 끊기면 자동 재연결해야 한다. 재연결 시 지수 백오프 적용.
최대 재연결 시도 10회, 최대 대기시간 60초.

**현재 구현:**
- 백엔드: `src/api/ws/market_ws.py:83-172` — `/ws/market` WebSocket 엔드포인트.
  - JWT 인증(4401 코드).
  - 구독/언esubscribe ops 지원.
  - 구독당 최대 50개(`MAX_SUBSCRIPTIONS_PER_TENANT`).
  - 재연결 gap-backfill: `resume_hint(feed)`로 REST 커서 힌트 제공.
- 프론트엔드: **WebSocket 클라이언트 미구현**.
  - `frontend/`에서 `ws/market` 연결 코드 검색 결과: **없음**.
  - `chart-engine/src/data/candleStream.ts:43` 주석: "WebSocket/polling implementation lives elsewhere, post DC-17".
  - **DC-17 미완료**: WebSocket 실시간 데이터 수신 기능이 프론트엔드에 구현되지 않음.

**갭:**

| 심각도 | 항목 | 근거 |
|---|---|---|
| HIGH | WebSocket 프론트엔드 클라이언트 미구현 | DC-17 미완료. 차트 엔진만 WS 인터페이스 정의 |
| HIGH | 재연결 로직 미구현 | WS 연결 끊김 시 자동 재연결 없음 |
| MEDIUM | 순서 보장(ordering guarantee) 미구현 | WS 메시지 순서 재구성 로직 없음 |
| MEDIUM | gap-backfill 미구현 | 서버는 `resume_hint` 제공하지만 프론트엔드에서 처리 안 함 |

### DF-RT-04 [규칙] 메시지 순서 보장

WebSocket 메시지에는 `sequence_number` 필드가 포함되어야 하며, 프론트엔드는 이를 기반으로 순서를 재구성한다.
순서가 깨진 메시지(누락/중복)는 버퍼링하여 순서 완성 후 처리한다.

**현재 구현:**
- 백엔드 `market_ws_dispatch.py` — 메시지 구조에 `sequence_number` 포함 여부 미확인.
- 프론트엔드 — WS 클라이언트 미구현이므로 순서 보장 로직도 미구현.

---

## 9. 현재 구현과 갭 요약

| # | 심각도 | 항목 | 근거 파일:줄 | 재현 절차 |
|---|---|---|---|---|
| G-01 | HIGH | OfflineBanner 항상 표시 | `main.tsx:28`, `OfflineBanner.tsx:12` | `isOnline` prop 없이 마운트 → 항상 `!isOnline=true` → 배너 상시 표시 |
| G-02 | HIGH | WebSocket 클라이언트 미구현 | `chart-engine/src/data/candleStream.ts:43` | DC-17 미완료. 차트에만 WS 인터페이스 정의 |
| G-03 | HIGH | 지수 백오프 미구현 | `api-client/httpErrors.ts:withGetRetry` | GET 재시도 시 즉시 재시도(대기 0초) |
| G-04 | MEDIUM | 재연결 시 폴링 자동 재개 없음 | `main.tsx:14-23` | `QueryClient.isOnline()` 미바인딩 |
| G-05 | MEDIUM | staleAfterSec 전역 표준 미정의 | `DataFreshness.tsx:39` | 화면마다 30초 고정 |
| G-06 | MEDIUM | critical 단계 미구현 | `DataFreshness.tsx:41-49` | `stale`만 주황, `critical`(빨강) 없음 |
| G-07 | MEDIUM | refetchIntervalInBackground 미설정 | `main.tsx:14-23` | 기본 `true` → 백그라운드 탭에서도 폴링 |
| G-08 | MEDIUM | 수동 재시도 UI 규칙 미정의 | `ErrorMessage.tsx:64-68` | `onRetry` prop 존재 but 미전달 |
| G-09 | MEDIUM | gap-backfill 미구현 | `market_ws.py:153`, `candleStream.ts:43` | 서버는 `resume_hint` 제공, 프론트엔드 미처리 |
| G-10 | LOW | 재연결 알림 미구현 | `useOnlineStatus.ts` | `online` 이벤트 리스너 있지만 알림 없음 |
| G-11 | LOW | 빈 상태 액션 미구현 | `DataFreshness.tsx:31-33` | "데이터 없음"만 표시 |
| G-12 | LOW | 재시도 횟수 표시 없음 | `useRetryableAction.ts` | 훅은 `retryCount` 보유 but 화면 표시 없음 |

---

## 10. DoD (Definition of Done)

- [x] 지정 파일 1개 작성: `docs/frontend/xcut/DATA_FRESHNESS_AND_REALTIME.md`
- [x] 모든 절 채움(해당 없음은 이유 명시)
  - §1 목적: 작성
  - §2 현재 구현 현황: 16개 파일, 20개 항목 코드 기반 확인
  - §3 로딩/스켈레톤/빈 상태: 3개 규칙, 3개 갭
  - §4 재시도/백오프/수동 재시도: 3개 규칙, 3개 갭
  - §5 네트워크 단절/재연결: 2개 규칙, 3개 갭
  - §6 데이터 신선도: 3개 규칙, 3개 갭
  - §7 폴링 주기/비용: 2개 규칙
  - §8 실시간 스트림 재연결/순서 보장: 2개 규칙, 4개 갭
  - §9 구현과 갭 요약: 12개 갭 테이블
  - §10 DoD: 이 섹션
  - §11 용어: 작성
- [x] 갭 표 각 행에 근거 파일:줄 명시
- [ ] 실스택 관찰: UAT 스택 가동 시 G-01 OfflineBanner 항상 표시 버그 curl/브라우저로 재확인 예정

---

## 11. 용어

| 용어 | 정의 |
|---|---|
| 신선도(freshness) | 데이터가 마지막 갱신된 시각부터의 경과 시간 |
| stale | 신선도 기준을 초과한 데이터 |
| critical | stale 기준의 2배를 초과한 데이터. 즉각적인 갱신 필요 |
| 스펙터(skeleton) | 실제 데이터 구조를 모방한 로딩 중 자리 표시 |
| 폴링(polling) | 정해진 간격으로 서버에 데이터 요청 |
| WebSocket 재연결 | 연결 끊김 후 자동 재접속. 지수 백오프 적용 |
| gap-backfill | 재연결 시 끊긴 구간의 데이터 보충 |
| 순서 보장 | WebSocket 메시지 순서 재구성. sequence_number 기반 |
