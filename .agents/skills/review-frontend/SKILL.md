---
name: review-frontend
description: 프론트엔드 상태관리, 접근성, API 계약 대응, 오프라인/에러 상태 축 검토 체크리스트
paths:
  - "frontend/apps/web/**"
  - "frontend/packages/**"
tier: [M]
axis: frontend
---

# review-frontend

## 적용 조건

`frontend/apps/web/`, `frontend/packages/`(shared-types, api-client)를 건드릴 때 적용한다.
`docs/design/17_frontend_architecture.md` §17.2·§17.4·§17.6, `ADR-2026-09-06-D`가 규범이다.

## 체크리스트

1. 서버 상태는 TanStack Query, 클라이언트 전용 UI 상태는 Zustand로 분리되어 있다 —
   서버에서 받은 데이터를 Zustand 전역 store에 그대로 복제해 두 소스가 어긋나는 패턴이
   없다 [근거: spec:17_frontend_architecture.md#17.4] [검사: 후보]
2. 백엔드 API 계약(OpenAPI) 변경이 `packages/shared-types`·`packages/api-client`에 동기화
   되어 있다 — 프론트가 백엔드 응답 필드를 문자열 리터럴로 직접 파싱하지 않는다
   [근거: spec:17_frontend_architecture.md#17.2] [검사: scripts/check_consistency.py::check_openapi_frontend]
3. 백엔드가 반환하는 `conflicting_states`류 구조화 에러가 필드 단위 에러로 매핑되어
   사용자에게 보여진다(원문 에러 문자열을 그대로 노출하지 않는다)
   [근거: spec:17_frontend_architecture.md#17.5.1] [검사: 후보]
4. 위험등급 경고, kill switch 등 안전장치 UI는 색상에만 의존하지 않고 텍스트로도 상태가
   명확하다(색맹 접근성) [근거: spec:17_frontend_architecture.md#17.6] [검사: 후보]
5. 포커스 순서, 레이블(`aria-label`/`label` 연결), 명도 대비가 CI 접근성 baseline 검사를
   통과한다 [근거: UX-4] [검사: 후보]
6. 로딩/에러/빈 상태(empty state) 3종이 모든 데이터 조회 화면에 명시적으로 처리된다 —
   `undefined`/`null` 접근으로 렌더링이 깨지는 경로가 없다
   [근거: spec:17_frontend_architecture.md#17.5] [검사: 후보]
7. 낙관적 업데이트(optimistic update)를 쓰는 화면은 실패 시 롤백 경로가 있다 — 서버
   거부 응답이 와도 UI가 성공 상태로 남아있지 않는다 [근거: spec:17_frontend_architecture.md#17.4]
   [검사: 후보]
8. 금액·수량 표시는 부동소수 연산 없이 서버가 보낸 문자열/Decimal 표현을 그대로 포맷팅
   한다(클라이언트에서 `parseFloat` 후 재계산해 반올림 오차를 만들지 않는다)
   [근거: 11_implementation_rules_v1.2.md] [검사: 후보]
9. 반응형 브레이크포인트 회귀가 없다 — 모바일 레이아웃에서 핵심 액션(주문 제출, kill
   switch)이 가려지거나 클릭 불가 영역에 놓이지 않는다 [근거: UX-21] [검사: 후보]
10. 라우팅 테이블에 정의된 화면 진입점이 실제로 배선되어 있다 — 컴포넌트는 존재하는데
    라우트에 연결되지 않아 도달 불가능한 화면이 없다
    [근거: spec:17_frontend_architecture.md#17.3] [검사: 후보]
11. 국제화 문자열 키가 `catalog.en` 등 카탈로그에 등록되어 있다 — 하드코딩된 UI 문자열이
    새로 추가되지 않는다 [근거: UX-2] [검사: 후보]
12. 커버리지 래칫이 후퇴하지 않는다(새 컴포넌트가 기존 커버리지 baseline보다 낮은 비율로
    테스트되지 않는다) [근거: ADR-2026-09-09-D] [검사: scripts/frontend_coverage_ratchet.mjs]

## 반례

### 반례 1 — 서버 응답을 전역 store에 복제해 두 소스가 어긋남

```tsx
// BAD: useQuery 결과를 그대로 zustand store에 복사 -- 캐시 무효화 후에도 store가 stale.
useEffect(() => {
  if (data) setPositionsInStore(data);
}, [data]);

// GOOD: 서버 상태는 useQuery가 단일 소스, zustand는 UI 전용 상태만 보관.
const { data: positions } = useQuery(["positions"], fetchPositions);
```

### 반례 2 — 색상만으로 위험 상태를 표시

```tsx
// BAD: 빨간 배경색만으로 kill switch 활성 상태를 표시 -- 색맹 사용자가 구분 못한다.
<div className="bg-red-500" />

// GOOD: 텍스트 라벨 + 아이콘을 함께 노출.
<div className="bg-red-500" role="status" aria-label="Kill switch 활성">
  <WarningIcon /> Kill switch 활성
</div>
```

## 이 축에서 났던 사고

- 접근성 baseline(포커스·레이블·대비)이 없어 CI에서 회귀를 못 잡던 상태에 검사를
  신설 (git:a4cf25a2)
- 대시보드가 문서 섹션 번호로 리프 목록을 찾다가 문서 구조가 바뀌자 "0/0"으로 표시되던
  결함 — 섹션 번호가 아니라 '리프 목록' 헤더 텍스트로 찾도록 수정
  (git:c4d3561c)
- `useAuthStore`가 `localStorage`가 `undefined`인 환경(SSR/프라이빗 모드)에서 방어 없이
  접근해 초기화가 깨지던 결함에 방어 코드 + 회귀 테스트를 추가 (git:028d089a)
- 계약 드리프트 가드에 `walletBalance` 응답 파서가 등재되지 않아 백엔드 스키마 변경을
  프론트가 감지 못하던 커버리지 공백을 래칫으로 봉쇄 (git:581e86b2)
