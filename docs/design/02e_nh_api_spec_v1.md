# 02e. NH투자증권(NH Investment Securities) Open API 구현 스펙 — v1.0

Spec 상위: `02_exchange_adapter_v1.3.md`. Bitget(`02b`/`02c`)/KIS(`02d`)와
동일한 "스펙 먼저, 그 다음 구현" 방법론 — 단, NH는 참고할 기존 코드가
전혀 없어(사용자 확인: "완전히 처음부터") 이번이 최초 조사다.

## 0. 배경 — API 브랜드 조사(WebFetch, 2026-09-03)

NH투자증권은 API 브랜드가 **두 세대**로 나뉜다는 것을 이번 조사에서
처음 확인했다:

1. **QV Open API**(레거시) — `wmca.dll` 기반 Windows COM/이벤트 아키텍처,
   공동인증서 로그인. **REST가 아니다** — Wine/Xvfb 없이는 Linux 서버
   환경(AIOS 배포 대상)에서 실행조차 안 된다. 커뮤니티 Rust 래퍼
   (`bekker/qvopenapi-rs`)로 이 사실을 확인함.
2. **NAMUH PLUG OpenAPI**(`nhplug.com`, 신규) — 진짜 REST+WebSocket API.
   공식 Python SDK(`github.com/PLUG-OpenAPI/nhplug-sdk`)가 존재하고
   실제 요청/응답 코드까지 확인 가능했다.

**이 문서는 (2) NAMUH PLUG만 다룬다** — (1)은 AIOS의 서버 배포 환경과
근본적으로 안 맞아(Windows GUI 이벤트 루프 의존) 이 어댑터 구조로는
구현할 수 없다. 사용자가 나중에 QV Open API를 요구하면 완전히 다른
설계(별도 Windows 워커 프로세스 + IPC)가 필요하다는 걸 여기 명시해둔다
(침묵 배제 아님).

## 0-1. 후속 조사(공식 API 가이드 포털 직접 확인, 2026-09-03, task-106)

`www.nhplug.com`의 실제 API 가이드 페이지(사이드바 아코디언, `/apiservice`
경로는 클라이언트 라우팅이라 직접 진입 불가 — 루트에서 좌측 메뉴로
진입해야 함)를 브라우저로 직접 열람해 §0의 SDK README 기반 추정 중
일부를 검증/수정한다.

**중대 발견 — 모의투자 도메인 자체가 "미제공"으로 명시됨.** 접근토큰
발급(`POST /oauth2/token`) 페이지의 "기본정보" 표에 `운영 도메인:
https://api.nhplug.com:8443`, `모의투자 도메인: 미제공`이 나란히
적혀 있다 — SDK README가 언급한 `moapi.nhplug.com`은 이 공식 가이드
페이지 기준으로는 최소 토큰 발급 엔드포인트에 한해 확인되지 않는다
(SDK가 실제로는 있지만 이 문서에 안 적었을 가능성도 배제할 수 없어
"거짓"이 아니라 "확인 불가"로 취급). **PM 배정 원칙**("is_paper_trading/
is_sandboxed는 실제로 확인될 때만 True, 확인 안 되면 False") 그대로
적용해 `NHAdapter.is_paper_trading`/`is_sandboxed`를 **항상 False**로
바꾼다 — 생성자의 `is_paper_trading` 플래그는 도메인 선택(호스트
URL) 용도로는 여전히 유효하지만, "이 adapter가 안전한 샌드박스"라는
보증은 더 이상 제공하지 않는다(Executor의 PAPER 전용 게이트가 이
adapter를 항상 차단하게 됨 — 안전 방향의 보수적 선택).

**엔드포인트 카테고리명 확인(경로 문자열 자체는 여전히 미확인)**:
좌측 메뉴에서 아래 카테고리 존재를 직접 확인함:
- `국내_주식_주문_정정`, `국내_주식_주문_취소` — §3의 "추정" 엔드포인트가
  실제로 존재하는 기능이라는 것은 확인됐으나, 정확한 URL 경로는
  아코디언 UI 조작 문제로 이번 세션에서 끝까지 열람하지 못했다.
- `국내_주식_조회_체결`, `국내_주식_조회_잔고`, `국내_주식_조회_매수가능수량`,
  `국내_주식_조회_매도가능수량`, `국내_주식_조회_예약주문`,
  `국내_주식_조회_실현손익`, `국내_주식_조회_자산현황`,
  `국내_주식_조회_실현손익추이`, `국내_주식_조회_종목별실현손익`,
  `국내_주식_조회_통합증거금`, `국내_주식_조회_권리보유`,
  `국내_주식_조회_권리예정` — **"주문조회"라는 이름의 카테고리는 없다.**
  §3에서 추정한 `/krstock/inquiry/v1/orderHistory`는 존재하지 않는
  카테고리에 붙인 이름일 가능성이 높다 — 실제로는 `국내_주식_조회_체결`
  (체결/실행 조회)이나 `국내_주식_조회_예약주문`(미체결/예약 주문 조회)
  중 하나가 그 역할을 할 것으로 보인다. **`get_order()`의 현재 구현은
  이 발견을 반영해 "라이브 검증 필요" 경고를 한 단계 더 강하게
  올린다** — 단순 필드 불일치 수준이 아니라 경로 자체가 틀렸을 가능성.

이 이상의 경로 확정은 SPA 아코디언 메뉴를 안정적으로 조작하는 데
실패해(동적 DOM ref가 클릭할 때마다 바뀌고, 부모를 펼치지 않으면
자식이 `display:none`이라 클릭 불가) 이번 세션에서 마무리하지 못했다
— 후속 조사(헤드리스 워커 인계, task-106 note 참조) 필요.

## 1. 신뢰도 표기 원칙

이번 조사는 KIS(02d)보다 참고자료가 훨씬 적다 — 공식 SDK가 있지만
스니펫이 매매(현재가/잔고/매수/매도)만 다루고 **정정·취소·주문조회는
예제 자체가 없다**(레포 3곳을 확인했지만 발견 못함). 그래서 이 문서는
각 엔드포인트에 신뢰도를 명시한다:
- **확인**: 공식 SDK 소스코드로 실제 경로/필드명 확인
- **추정**: 확인된 엔드포인트의 명명 관례를 그대로 연장한 추측 —
  라이브 검증 전까지 틀릴 수 있음

## 2. 공통 사항(확인)

| 항목 | 값 |
|---|---|
| 실전 REST | `https://api.nhplug.com:8443` |
| 모의투자 REST | `https://moapi.nhplug.com:8443` |
| 실전 WS(국내) | `wss://api.nhplug.com:7070` |
| 실전 WS(해외) | `wss://api.nhplug.com:7080` |
| 모의투자 WS | `wss://moapi.nhplug.com:17070` |
| 토큰 발급 | `POST {host}/oauth2/token`, `application/x-www-form-urlencoded`, params `{appkey, appsecretkey, grant_type:"client_credentials", scope:"oob"}` → `{access_token, expires_in}`(기본 86400초=24시간) |
| 요청 헤더 | `x-client-id`(appkey) / `x-client-secret`(appsecretkey) / `authorization: Bearer {token}` / `content-type: application/json; charset=UTF-8` — Bitget/KIS와 달리 **tr_id를 헤더로 보내지 않는다**(엔드포인트 자체가 TR 구분) |
| 성공 판정 | HTTP 200이어도 실패일 수 있음 — 응답 바디의 `rsp_cd`가 `"00000"`(그 외 `"00166"`/`"00221"`/`"13578"`도 성공으로 취급하는 게 SDK 관례) 또는 `rsp_msg`에 "완료" 포함 시 성공 |
| 계좌구분 | 계좌번호 앞 2자리 코드: `01`/`02`=실전(운영 도메인), `03`=모의투자(모의 도메인) — **도메인과 계좌구분을 맞춰야 함**(운영 도메인에 03 계좌 쓰면 실패) |
| Rate limit | 초당 4~5회 권장(공식 문서 수치는 미확인, SDK 기본값) |

## 3. 국내주식(krstock) 엔드포인트

| 함수 목적 | Method | Path | 신뢰도 | 비고 |
|---|---|---|---|---|
| 현재가 조회 | POST | `/krstock/quote/v1/currentPrice` | 확인 | params `{iem_cd, market_cd:"KRX"}` |
| 잔고 조회 | POST | `/krstock/inquiry/v1/balance` | 확인 | params `{act_no, bnc_bse_cd:"5", ltg_aot_dit_cd:"9", aet_bse:"2", qut_dit_cd:"UNT"}` — 응답 필드명은 SDK 예제에 없음(라이브 검증 필요) |
| 계좌 목록 조회 | POST | `/n2/acctinfo` | 확인(경로만, 파라미터 미확인) | |
| 매수가능조회 | POST | (경로 미확인, `buyable_quantity` 스니펫 존재) | 확인(존재만) | |
| 매도가능수량조회 | POST | (경로 미확인, `sellable_quantity` 스니펫 존재) | 확인(존재만) | |
| 매수 주문 | POST | `/krstock/order/v1/cashBuy` | 확인 | body `{act_no, iem_cd, orr_qty, orr_pr, nmn_pr_tp_cd:"01"(지정가)\|"05"(시장가), orr_cnd_dit_cd, ssl_nmn_pr_dit_cd, rmt_mkt_cd:"KRX", sor_mkt_sli_yn}` |
| 매도 주문 | POST | `/krstock/order/v1/cashSell` | 확인(경로 패턴, 파라미터는 매수와 동일 관례로 추정) | |
| 정정 주문 | POST | `/krstock/order/v1/cashModify`(추정) | **추정** | 공식 예제 없음 — cashBuy/cashSell 명명 패턴을 그대로 연장 |
| 취소 주문 | POST | `/krstock/order/v1/cashCancel`(추정) | **추정** | 공식 예제 없음 |
| 주문 조회 | POST | `/krstock/inquiry/v1/orderHistory`(추정) | **추정, 신뢰도 낮음** | §0-1 재확인 — 공식 가이드 메뉴에 "주문조회"라는 이름의 카테고리 자체가 없다(`국내_주식_조회_체결`/`국내_주식_조회_예약주문`이 그 역할을 대신할 가능성). 필드 불일치가 아니라 경로 자체가 틀렸을 위험 |
| 일별 시세 | POST | `/krstock/quote/v1/...`(경로 미확인, `current_daily` 스니펫 존재) | 확인(존재만) | |

## 4. WebSocket(확인)

| 항목 | 값 |
|---|---|
| 접속 URL | `wss://{host}:{port}/websocket` — 국내시세 7070, 해외시세 7080, 모의투자 17070 |
| 구독 메시지 | `{"header": {"token": "<access_token>", "tr_type": "1"}, "body": {"tr_cd": "<channel>", "tr_key": "<종목코드 등>"}}` — `tr_type` "1"=등록, "2"=해제 |
| 실시간 체결가(국내, KRX+NXT 통합) | `tr_cd="mc"`, tr_key=6자리 종목코드 |
| 실시간 호가(국내, 통합) | `tr_cd="mb"`, tr_key=6자리 종목코드 |
| 실시간 체결통보(국내) | `tr_cd="d2"`, tr_key=사용자ID 또는 공백 — 실제 주문 체결 시에만 발생 |
| 해외주식 실시간 체결 | `tr_cd="RC"`, tr_key=GIC 15자리 코드(종목코드 아님) | |
| 해외주식 체결통보 | `tr_cd="d0"` | |

메시지 응답 바디 형식(JSON인지 KIS처럼 구분자 텍스트인지)은 이번
조사에서 확인하지 못했다 — 공식 SDK의 `nhplug/realtime.py` 소스를
추가 조사해야 확정 가능(다음 리프에서 필요시 재조사).

## 5. Phase 1 스콥 결정

06번 §6.1-A 원칙 재확인 + KIS(02d)와 동일 판단:
- **포함(P0)**: 국내주식(krstock) 시세·잔고·매수/매도, WebSocket 실시간
  체결가/호가/체결통보
- **P1**: 정정·취소·주문조회(추정 엔드포인트라 P0보다 낮춤 — 확실한
  것부터 완료조건에 넣는다), 매수/매도가능조회, 일별시세, 계좌목록
- **P2(스콥 밖, 명시)**: 해외주식(gbstock), 파생상품(선물옵션), 채권,
  금현물 — 전부 사용자 요청("모든 기능")에 해당하지만 이번 리프는
  국내주식 P0+P1까지만("100%"는 KIS/Bitget과 동일하게 단계적 완료,
  Bitget 02c처럼 후속 리프로 확장 가능)

## 6. 완료조건

- §3의 확인된 엔드포인트(현재가/잔고/매수/매도) + 추정 엔드포인트
  (정정/취소/조회)가 `NHAdapter`에 실제 메서드로 존재, MockTransport
  테스트로 검증됨.
- `ExchangeAdapter` ABC 전체 메서드(12개, KIS/Bitget과 동일 계약)
  구현 — 불가능한 것(WebSocket 등)은 KIS처럼 부분 구현 가능.
- 추정 엔드포인트는 코드 docstring에 "확인 안 됨, 라이브 검증 필요"를
  명시(정직한 최선 추정치 원칙).
- 실제 NH API 키 확보 후 라이브 1회 왕복 검증은 미확보 상태로 보류
  (Bitget/KIS와 동일 제약).

## 참고 문헌
- NAMUH PLUG OpenAPI 포털: https://www.nhplug.com
- 공식 Python SDK: https://github.com/PLUG-OpenAPI/nhplug-sdk
- (참고, 이 문서 범위 밖) 레거시 QV Open API 커뮤니티 래퍼: https://github.com/bekker/qvopenapi-rs
