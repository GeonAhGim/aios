# 프런트-백엔드 배선 점검 결과 (2026-11-19, 실스택)

> 방법: UAT 스택(실제 API·DB)에 새 사용자로 접속해 (a) `scripts/uat/wiring_probe.py`로 파라미터 없는 GET 55개를 전수 호출,
> (b) 브라우저(Playwright)로 J1~J3 화면을 열어 실제로 나가는 API 호출과 응답을 관찰, (c) 저장소의 정적 일관성 검사 결과를 합쳤다.
> 생성 보고서: `WIRING_REPORT.md`(도구가 다시 만든다). 아래는 사람이 읽는 결론이다. **관찰한 사실과 미확인을 구분해 적는다.**

## 1. 결론 (사용자 불안에 대한 답)

- **읽기 경로는 실제로 배선돼 있다.** 화면이 부르는 `/executions`, `/portfolio`, `/v1/positions`, `/v1/foundation/mandates/status`,
  `/exchange-credentials`, `/notifications/history` 등이 실서버에서 200을 돌려주고, 빈 상태도 서버의 실제 값(0, 빈 목록)으로 그려진다.
  GET 55개 중 **35개가 정상 응답**, 11개는 권한·MFA·위임장 같은 게이트(관리자 전용 등), 8개는 파라미터가 필요, 1개는 "아직 평가 전" 404(정상).
- **그러나 일부 화면은 백엔드가 없다.** 프런트 클라이언트가 부르는데 백엔드에 경로가 없는 것이 **16개**(아래 §2). 메뉴에 이름이 노출된 화면과 겹친다.
- **쓰기 경로(POST 94개)와 경로 변수 GET 24개는 이번에 점검하지 못했다**(미확인). 다음 단계: 쓰기 경로를 여정별 실스택 e2e로 검증한다.
- **테스트 층에도 가짜가 있었다**: 로컬 워커가 쓴 J3 e2e가 `await` 없는 `isVisible()`을 조건에 써서 화면과 무관하게 통과하는 형태였다(§3 F5).

## 2. 프런트가 부르지만 백엔드에 없는 경로 (런타임 실패 확정)

정적 검사(`openapi_client_mismatch`)가 기준선으로 묵혀 두던 목록:

| 영역 | 경로 | 영향 화면(추정, 확인 필요) |
|---|---|---|
| 세션 | `/auth/sessions`, `/auth/sessions/:sessionId` | 설정의 세션 관리 |
| AI | `/v1/ai/experiments`, `/v1/ai/providers`, `/v1/ai/providers/:provider`, `/v1/ai/proposals/:id:promote`, `...:promote-ticket` | AI 스튜디오(`/ai/studio`) |
| 팔로우 | `/v1/foundation/follow/subscriptions` (+ 상세, 성과) | 팔로우(`/follow`) |
| 신호 | `/v1/foundation/signals/sources` (+ receipts, disable, rotate) | 신호 소스(`/signals/sources`) |
| What-if | `/v1/foundation/whatif/preview-order`, `/v1/foundation/whatif/rebalance-plan` | What-if 화면 |

또한 계약 파일(`contracts/openapi/v1.json`)에는 있으나 구현이 없는 항목 18개(AI 어시스턴트 `generate-script`·`explain-*`, 개인 모드 `promote`·`kill`·`bundle`, 옵션 체인, 리서치 항목 등)가 있다.
-> 전체 목록은 `WIRING_REPORT.md` 하단. **처리 방침**: MVP-1 여정(J1~J3)에 속하지 않는 화면은 "백엔드 준비 전"임을 화면에서 숨기거나 안내(기능 플래그), 속하면 구현 리프. 화면별 결정은 Phase 0 화면 명세 갭 표에서 확정한다.

## 3. 이번에 발견한 결함 (실스택 관찰 근거)

| # | 증상 | 근거 | 귀속 | 처리 |
|---|---|---|---|---|
| F1 | `/readyz`가 503: 청산 워커가 `AIOS_LIQUIDATION_SEED_KEY` 없이 3초마다 fail-closed 거부 | 컨테이너에서 직접 호출해 예외 확인, 키 주입 후 200 | 배포 설정(생성기) | **수정 완료**(8b9e24f4c). 운영 배포 체크리스트에도 필요(.env.example엔 있음) |
| F2 | 앱 전체 `logger.exception(...)`이 운영 로그에서 스택 트레이스를 잃음 | `JSONLinesFormatter`가 `exc_info`를 출력하지 않음, 로그에 `LiquidationSeedKeyMissingError` 0건 | 백엔드 관측성 | 리프 발행 |
| F3 | 모의(PAPER) 주문도 거래소 자격증명이 없으면 서버가 404로 거부. 화면은 "요청한 항목을 찾을 수 없습니다"만 표시 | `/executions` 생성 시도: `POST /executions -> 404 "bitget 자격증명이 없거나 해지되었습니다."` | 화면 명세(J3)+XCUT 오류 표시 | 명세 갭 + 제품 결정 필요(키 없는 내장 모의 거래소) |
| F4 | 상단 메뉴에 번역 키 `nav./signals/sources`가 그대로 노출 | 메뉴 스냅샷 | 화면(i18n) | 리프 발행 |
| F5 | J3 e2e가 가짜로 통과하는 형태(`isVisible()` 미대기 조건) | uat-j3.spec.ts 코드 | e2e | 리프 발행(재작성) |
| F6 | J3 첫 경로 안내 부재: 전략 ID를 어디서 얻는지, 위임장 설정이 선행 조건임을 알려 주지 않음(위임장 없으면 모든 주문 차단) | `/executions` 폼(전략 ID 자유 입력), `/mandates` 화면 | 화면 명세(J3) | 명세 갭 |
| F7 | 속도 제한 429가 MFA 설정 화면에서 "요청이 너무 많습니다"로 표시됨(정상 동작이나 재시도 안내 없음) | e2e 대량 가입 시 관찰 | XCUT 오류 표시 | 명세 갭 |

## 3-b. 쓰기 경로·경로 변수 점검 결과 (`scripts/uat/write_probe.py`, 생성 보고서 `WIRING_WRITES_REPORT.md`)

임시 계정으로 로컬 UAT에 **빈 JSON 본문·가짜 UUID**를 보내 110개 작업(쓰기 + 경로 변수 GET, `/admin`·`/auth` 제외)을 점검했다.

| 결과 | 건수 | 의미 |
|---|---|---|
| 입력 검증 응답(400/422) | 66 | 라우트와 검증이 실제로 존재(정상) |
| 권한 게이트(401/403) | 9 | 접근 가능하나 역할·MFA 등 필요 |
| 대상 없음(404) | 32 | 가짜 UUID라 정상(경로 변수 작업) |
| **서버 오류(5xx)** | **2** | **결함**: `POST /v1/foundation/mandates/mandate:pause`, `:resume` — 위임장 없는 사용자에게 404가 아니라 500 |
| 빈 본문 수락(2xx) | 1 | `PUT /notifications/preferences` — 검토 대상 |
| 스텁/미구현(501·스텁 문구) | 0 | |

**F8 (신규, 수정 리프 발행)**: 위임장 일시정지·재개 500. 원인: `NoActiveMandateError`가 `evaluate_policy`, `pause_mandate`, `propose_amendment`에 같은 이름으로 따로 정의돼 있고, 예외->HTTP 매핑 레지스트리는 클래스 동일성으로 하나만 등록 -> 나머지는 매핑 없음 -> 500. 같은 패턴(같은 이름 예외의 다중 정의)이 다른 컨텍스트에도 있는지 점검이 필요하다(후속).

## 4. 다음 단계

1. 쓰기 경로 실스택 검증: 여정별 e2e(J1 완료, J2·J3 재작성) + 쓰기 경로 계약 점검 도구 확장.
2. §2 목록을 화면 명세 갭 표와 대조해 화면별 결정(숨김/구현/제거).
3. 이 도구를 CI/야간 점검에 연결해 `missing`·`server_error`가 늘면 알린다(래칫).
