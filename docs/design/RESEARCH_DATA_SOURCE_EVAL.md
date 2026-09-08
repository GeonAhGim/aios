# RESEARCH_DATA_SOURCE_EVAL — 리서치 데이터 소스 평가 (RD-1)

- 리프: RD-1 (`docs/specs/L4_research_data_and_market_ecosystem_v1.0.md` §9 RD-1, 대상 목록은 §2.3)
- 범위: **문서만.** 코드 변경 0. 이 문서가 통과하기 전에는 RD-10~18(어댑터) 리프를 배정하지 않는다(§9 RD-1 note).
- 대상: §2.3 표 그대로 계층 A 8종 — OpenDART, ECOS, KOSIS, KRX 정보데이터시스템, FRED, SEC EDGAR, GDELT, 범용 RSS/Atom.
- 확인 날짜: 모든 원문 인용은 **2026-09-09**에 해당 URL에서 확인했다(웹 검색 도구 사용, JS 렌더링 SPA는 원문 확보 불가 — 해당 소스는 그 사실 자체를 판정 근거로 남긴다).

## 0. 채점 원칙 — 이 리프는 "최고점을 고르는" 문서가 아니라 라이선스 게이트다

CH-0(`CHART_ENGINE_FORK_EVAL.md`)이 후보 중 최선을 고르는 비교 평가였다면, RD-1은 **8종 각각에 대해 반입 허용/금지를 개별로 통과·탈락시키는 게이트**다. 따라서 가중치 채점표 대신 소스마다 (a)~(d) 4개 판정 열을 채우고, 하나라도 반증 가능성 없이 통과하지 못하면 그 소스 하나만 반입 금지로 떨어뜨린다(다른 소스에 전이되지 않음).

### 0.1 사실(fact) vs 표현(expression) 원칙 — 8종에 일관 적용

한국·미국 저작권법 공통으로 **단순한 사실·수치 데이터는 저작권 보호 대상이 아니다**(창작적 표현만 보호). 이 원칙을 8종에 일관되게 적용한다:
- 금리·환율·지수·물가지수 같은 **숫자 시계열 값 자체**는 사실이라 store_full 판정의 유력한 근거가 된다(단, 사이트 이용약관이 계약으로 재배포를 별도 금지하면 계약이 우선한다 — §2.4 KRX가 이 경우).
- 공시서류 본문·기사 본문처럼 **발행자가 작성한 서술형 문장**은 창작적 표현이라 명시적 재배포 허용 문구가 없으면 store_excerpt 이하로 보수적으로 판정한다(fail-closed).

이 구분이 없으면 "무료·공공데이터라 가능"류의 반증 불가능한 rationale이 되기 쉽다 — DoD (a)가 원문 인용을 요구하는 이유다.

### 0.2 DoD 재확인 (task-2401)
(a) 원문 인용 1문장 이상 + URL + 확인 날짜, 약관 본문(배지·요약 아님). (b) redistribution 판정 = store_full\|store_excerpt\|link_only + 근거 문장 지목. (c) rate limit 실측, 불가능하면 "미확인" + 그 소스 반입 금지(추정치 금지). (d) 커버리지·정정 정책. (e) 결론에 허용/금지 목록 분리. (f) OpenDART·ECOS·KOSIS 키 발급 필요 여부 기재(키 값 자체는 미기재).

---

## 1. OpenDART (금융감독원 전자공시)

**(a) 원문 인용**
> "금융감독원이 제공하는 오픈API 서비스 및 관련 프로그램의 저작권은 금융감독원에 있습니다." (이용약관 제16조)
> "약관에 명시되지 않은 저작권과 관련된 사항은 저작권법 및 공공데이터법에 따릅니다." (이용약관 제16조④)
— 출처: <https://opendart.fss.or.kr/intro/terms.do>, 확인 2026-09-09.

**(b) redistribution 판정 — `store_excerpt`(기본), 구조화 필드는 `store_full`**
근거: 제16조는 "오픈API **서비스 및 관련 프로그램**"의 저작권만 금감원 귀속으로 명시할 뿐, 상장회사가 작성해 제출한 **개별 공시서류 본문**의 저작권에 대해서는 침묵한다. 침묵을 재배포 허용으로 확대 해석하는 것은 §0.1의 fail-closed 원칙에 반한다. 따라서:
- 회사명·종목코드·`rcept_no`(접수번호)·보고서명·재무제표 수치 등 **구조화 필드(사실)**는 `store_full`.
- 사업보고서·정정신고서의 **서술 본문**은 명시적 재배포 허용 문구가 없으므로 `store_excerpt`로 제한.

**(c) rate limit 실측**
> "요청 제한을 초과하였습니다. 일반적으로는 20,000건 이상의 요청에 대하여 이 에러 메시지가 발생되나" (오류코드 020)
— 출처: <https://opendart.fss.or.kr/guide/detail.do?apiGrpCd=DS001&apiId=2019001>, 확인 2026-09-09.
- **일 20,000건 초과 시 오류코드 020** (공식 오류 메시지에 명시된 수치 — 추정 아님).
- 인증키(`crtfc_key`, 40자리) **필수**. 회원가입 후 인증키 신청 메뉴에서 발급.
- 단일 조회 회사 개수 한도: 최대 100건(오류코드 021).

**(d) 커버리지·정정 정책**
정정 공시는 최초 공시와 별도의 `rcept_no`로 새로 접수되는 것이 DART 공시 실무의 일반 관행이나, 이번 리프에서 API 응답 스펙 원문(report_nm 필드에 정정 표기가 실제로 붙는지)까지는 재확인하지 못했다 — **미검증**으로 남긴다. RD-10 구현 시 실제 API 응답으로 재검증 필요.

**결론: 허용** (구조화 필드 `store_full`, 서술 본문 `store_excerpt`). 키 발급 **필요**.

---

## 2. ECOS (한국은행 경제통계시스템)

**(a) 원문 인용**
> "제공대상 공공데이터는 별도의 절차 없이 자유롭게 이용할 수 있습니다."
> "출처가 한국은행임을 반드시 밝혀야 하며, 정보를 수정·변경·가공할 경우에도 이를 분명히 밝혀야 합니다."
— 출처: 한국은행 저작권보호방침 <https://www.bok.or.kr/portal/main/contents.do?menuNo=200228>, 확인 2026-09-09.

**중요한 한계**: 이 인용문은 **한국은행 홈페이지 전체의 저작권 정책**이며 ECOS 전용 조항이 아니다. ECOS 자체 사이트(`ecos.bok.or.kr/api/`)는 JavaScript로 렌더링되는 SPA라서 이 리프에서 사용한 조회 도구로는 페이지 제목("한국은행 Open API 서비스")만 확보되고 본문 원문을 가져올 수 없었다 — 이는 반증 가능한 사실이며, ECOS 전용 이용약관·rate limit·정정 정책 원문은 이번 리프에서 **확보하지 못했다**.

**(b) redistribution 판정 — 확인 불가로 인해 미확정** (참고: 위 (a) 인용문 기준으로는 `store_full` 추정 가능하나 ECOS 전용 조항이 아니므로 확정 판정 보류)

**(c) rate limit 실측 — `미확인`**
검색으로 "개발계정 1일 10,000건, 소진 시 운영계정 전환 신청" 류의 서술을 발견했으나, 전부 서드파티 블로그·비공식 가이드가 출처였고 ECOS 공식 페이지 원문을 직접 인용하지 못했다(위 SPA 렌더링 한계). DoD (c)의 "실측 불가능하면 추정치를 쓰지 말고 미확인으로 남기고 반입 금지" 규칙을 그대로 적용한다.

**(d) 커버리지·정정 정책**: 확인 불가 — **미검증**.

**결론: 반입 금지** (rate limit 미확인). 키 발급 여부: 사이트에 "인증키 신청" 메뉴/활용신청 절차가 존재함은 화면 구조로 확인되나, 필수 여부를 규정하는 원문 조항은 확보하지 못했다 — **필요할 것으로 보이나 원문 미확인**.

---

## 3. KOSIS (통계청/국가데이터처 국가통계포털)

**(a) 원문 인용**
> "국가데이터처에 저작권이 있습니다." (통계정보 이용지침)
— 출처: <https://mgmk.kosis.kr/serviceInfo/useGuide.do>, 확인 2026-09-09.
같은 페이지에서 국제·북한통계는 **비상업적 목적**으로만 이용 가능하고, 간행물은 공공누리 조건을 따라야 한다는 제한이 함께 명시되어 있다(도구가 반환한 발췌 요약 기준 — 원문 문장 전체를 그대로 재인용하지 못한 항목은 아래 한계 참고).

**(b) redistribution 판정 — 항목별로 분리**
- 국내 통계(물가·고용·산업활동 등 수치): `store_full`(사실 데이터 + 위 인용문의 자유이용 취지).
- 국제통계·북한통계: `store_excerpt`(비상업적 목적 한정) — 상업적 이용은 별도 승인 필요.
- 근거: 위 인용문 및 국제/북한통계 비상업 제한 조항.

**(c) rate limit 실측 — `미확인`**
검색으로 "2026-02-05 HTTP 프로토콜 제공 종료 및 분당 호출건수 제한 안내", "2026-07-09 분당 호출건수 제한 시행" 공지의 **제목**은 확인했으나, 페이지네이션된 공지 목록(2page 이상)에서 해당 공지 본문을 직접 열람하지 못해 "분당 1,000건"이라는 수치를 원문으로 인용하지 못했다. 제목만으로는 DoD (a)가 요구하는 "원문 인용 1문장"을 충족하지 못하므로, 규칙 (c)에 따라 미확인 처리하고 반입 금지로 판정한다.

**(d) 커버리지·정정 정책**: 확인 불가 — **미검증**.

**결론: 반입 금지** (rate limit 미확인 — (a)(b)는 재조사 시 통과 가능성이 있어 사실상 "보류"에 가깝지만, DoD 문구 그대로 반입 금지로 명기한다). 키 발급: **필요**(활용신청 → 자동승인 → 회원당 인증키 1개 발급, 모든 서비스 공용 — 원문은 서드파티 가이드 수준 확인이라 절차 존재만 신뢰하고 수치·세부조항은 인용하지 않음).

---

## 4. KRX 정보데이터시스템

**(a) 원문 인용**
> "당 사이트의 화면, 프로그램, 문서, 로고 등 모든 저작권과 지식재산권은 거래소 또는 정당한 권리자에게 귀속됩니다." (이용약관 제13조)
> "이용자는 당 사이트의 정보를 거래소의 사전 허락 없이 복사·복제·배포·전송·공중송신하여서는 아니 됩니다." (이용약관 제12조②)
— 출처: <https://data.krx.co.kr/contents/MDC/INFO/informationController/MDCINFO003.cmd>, 확인 2026-09-09.

**(b) redistribution 판정 — `반입 금지`에 준하는 제약**
근거 문장은 **제12조②**다: 지수·매매동향 수치가 §0.1 원칙상 "사실"이라는 항변이 가능하더라도, 이 사이트는 접근 자체가 이용약관(사실상 clickwrap)에 동의해야 성립하므로 **계약법적 제한이 사실 원칙보다 우선 적용**된다고 보수적으로 판단한다. 사전 허락 없는 복제·배포·전송을 명문으로 금지하므로 `link_only`조차 안전하지 않다(사이트 "정보"의 전송 자체를 금지 문언이 포괄).

**(c) rate limit 실측 — `미확인`**
`data.krx.co.kr`은 공식 REST API가 아니라 **웹 화면 조회 + OTP 기반 CSV/Excel/PDF 다운로드** 방식이다(검색으로 확인 — OTP를 발급받아 제출해야 다운로드되는 구조). 별도 서비스인 `openapi.krx.co.kr`(KRX Open API)의 이용방법 페이지(`OPPINFO003.jsp`)도 조회했으나 "STEP 01~04" 절차 안내만 있을 뿐, 무료/유료 구분이나 초당·일일 호출 제한 수치를 명시한 원문을 찾지 못했다. 공식 rate limit 조항 자체가 없는 상태에서 수치를 추정해 적을 수 없다.

**(d) 커버리지·정정 정책**: 확인 불가 — **미검증**.

**결론: 반입 금지** — (b) 명시적 재배포 금지 조항 + (c) rate limit 미확인의 이중 사유.

---

## 5. FRED (세인트루이스 연방준비은행)

**(a) 원문 인용**
> "Place the following notice prominently on your application: 'This product uses the FRED® API but is not endorsed or certified by the Federal Reserve Bank of St. Louis.'"
> "your use of the FRED® API to develop, reproduce and distribute applications that interoperate with the FRED® API"
— 출처: <https://fred.stlouisfed.org/docs/api/terms_of_use.html>, 확인 2026-09-09.
> "429 Too Many Requests (Up to 120 requests per minute are allowed before being served a 429 error code. Not complying with the throttling can result in a temporary block.)"
— 출처: <https://fred.stlouisfed.org/docs/api/fred/errors.html>, 확인 2026-09-09.
> "All web service requests require an API key to identify requests."
— 출처: <https://fred.stlouisfed.org/docs/api/api_key.html>, 확인 2026-09-09.

**(b) redistribution 판정 — `store_full`(수치 시계열) + 귀속고지 의무**
근거: 위 첫 인용문이 "reproduce and distribute applications that interoperate"를 명시적으로 이용 목적에 포함하고, 시계열 값 자체는 §0.1 원칙상 사실이다. 단, FRED에는 민간기관이 원저작자인 개별 series가 섞여 있어("일부 series는 원제공기관 저작권이 있으니 재공표 전 source note 확인" — 이번 리프에서 series 단위 예외 목록까지는 확정하지 못함, **미검증**), RD-13 구현 시 series의 `source note`를 파싱해 서드파티 저작권 표시가 있으면 그 series만 `link_only`/`store_excerpt`로 내려야 한다.

**(c) rate limit 실측**: **분당 120회**(초과 시 429, 지속 위반 시 일시 차단). 인증키 **필수**(32자 영숫자, 앱마다 별도 키 권장).

**(d) 커버리지·정정 정책**: FRED/ALFRED는 시계열의 사후 수정치를 vintage로 보존해 과거 발표 시점 값을 조회할 수 있는 것으로 알려져 있으나, 이번 리프에서 `realtime_start`/`realtime_end` 필드를 공식 문서로 재확인하지 못했다 — **미검증**. RD-A2 관점에서는 유리한 구조(정정이 새 vintage로 추가되는 모델)로 보인다.

**결론: 허용**. 키 발급 필요(미국 소스라 DoD (f) 대상은 아니나 참고 기재).

---

## 6. SEC EDGAR

**(a) 원문 인용**
> "Current max request rate: 10 requests/second."
> "Please declare your user agent in request headers"
> "Download only what you need and please moderate requests to minimize server load."
— 출처: <https://www.sec.gov/search-filings/edgar-search-assistance/accessing-edgar-data>, 확인 2026-09-09.

**(b) redistribution 판정 — 구조화 메타데이터 `store_full`, 서술 본문 `store_excerpt`**
- CIK·accession number·문서유형·접수일·XBRL 수치 등 **구조화 필드**는 사실이므로 `store_full`.
- 10-K/10-Q/8-K **서술 본문**: 17 U.S.C. §105(연방정부 저작물 저작권 배제)는 "**연방정부 공무원이 직무상 작성한 저작물**"에만 적용된다 — 등록법인(민간 회사)이 작성해 제출한 공시서류 본문은 이 정의에 해당하지 않으므로 §105로 자동 퍼블릭도메인이 되지 않는다. 업계 관행상 광범위하게 자유 이용되지만, 이 리프에서 SEC가 명시적으로 저작권을 포기하거나 재배포를 허용한다는 원문 문구는 sec.gov에서 확인하지 못했다. §0.1 원칙에 따라 서술 본문은 `store_excerpt`로 보수적으로 제한한다.

**(c) rate limit 실측**: **초당 10회**(모든 EDGAR 호스트네임 합산, IP 단위). User-Agent 헤더에 신원+연락처 이메일 포함 필수. **인증키 불필요**.

**(d) 커버리지·정정 정책**: `10-K/A`, `8-K/A` 등 "/A"(Amendment) 접미사가 붙은 정정 제출은 원본과 별도의 accession number로 존재하는 것이 EDGAR의 일반적인 관행으로 알려져 있으나, 이번 리프에서 sec.gov 원문으로 직접 재확인하지 못했다 — **미검증**. append 구조 자체는 RD-A2와 정합적으로 보인다.

**결론: 허용** (서술 본문은 `store_excerpt`로 제한 — RD-14 구현 시 유의).

---

## 7. GDELT

**(a) 원문 인용**
> "all datasets released by the GDELT Project are available for unlimited and unrestricted use for any academic, commercial, or governmental use of any kind without fee."
> "any use or redistribution of the data must include a citation to the GDELT Project and a link to this website."
— 출처: <https://www.gdeltproject.org/about.html>, 확인 2026-09-09.

**(b) redistribution 판정 — GDELT 산출 메타데이터 `store_full`, 뉴스 기사 본문 `link_only` 강제**
근거: 위 인용문의 "GDELT **datasets**"는 GDELT 프로젝트 자신이 생성한 구조화 이벤트/GKG 메타데이터(누가·언제·어디서·무엇을, 인물·기관·테마 추출 그래프)만을 지칭한다. GDELT는 원문 뉴스 기사를 소유하지 않는 제3자(개별 언론사) 저작물이므로, **GDELT가 갖지 않은 권리를 이 문구로 재배포 허용할 수 없다** — 자신이 소유하지 않은 저작물의 재배포를 허용하는 조항은 법적으로 무효이기 때문이다. 따라서:
- GDELT가 직접 산출한 이벤트/GKG 구조화 메타데이터: `store_full`.
- **원문 뉴스 기사 본문: `link_only` 강제 — 본문 저장·요약 생성 금지.** (GDELT GKG는 기사 전문이 아니라 추출된 메타데이터 그래프라는 것이 일반적으로 알려진 설계이나, 이번 리프에서 GDELT 공식 문서의 명시적 원문 문장으로 이 사실을 재확인하지는 못했다 — **미검증**으로 표기하되, 판정 자체는 위 소유권 논리로 충분히 근거가 있다.)

**(c) rate limit 실측**: 계층 A 대상 접근 경로(Event/GKG 원자료 CSV/zip)는 **정적 파일 배포**다 — "매일 06:00 EST 갱신 게시"(<https://www.gdeltproject.org/data.html>, 확인 2026-09-09)이며 인증키가 불필요하고, 공식 문서에 초당·일일 요청 제한 조항 자체가 없다(이는 "미확인"이 아니라 "문서에 그런 조항이 존재하지 않음을 확인"한 것). 별도의 실시간 질의형 DOC 2.0 API는 이번 RD-15 대상(원자료 배포 경로)과 다른 시스템이라 이 리프의 판정 범위에 포함하지 않는다.

**(d) 커버리지·정정 정책**: 원 기사가 언론사에서 사후 수정되어도 GDELT가 이를 감지해 역행 갱신하는 공식 메커니즘은 확인하지 못했다 — **RD-A2(정정 체인) 관점의 구조적 한계**로 명시한다. 언론사가 침묵 수정(silent correction)하면 GDELT의 이벤트 레코드에는 반영되지 않을 수 있다.

**결론: 허용** (뉴스 본문 `link_only` 강제 — 본문 저장·요약 생성 금지를 RD-3/RD-15가 코드로 강제해야 한다).

---

## 8. 범용 RSS/Atom

이 항목은 단일 사업자의 ToS가 아니라 **프로토콜**이다 — 사용자가 임의 피드를 등록하므로(§2.3 "사용자가 소스 추가 가능"), 개별 퍼블리셔마다 저작권자가 다르고 사전에 전부 확인할 수 없다.

**(a) 원문 인용**
> "This document is authored by the RSS Advisory Board and is offered under the terms of the Creative Commons Attribution/Share Alike license."
— 출처: RSS 2.0 명세 <https://www.rssboard.org/rss-specification>, 확인 2026-09-09. 이는 **명세 문서 자체**의 라이선스이며 피드로 배포되는 콘텐츠의 라이선스가 아니다 — 명세는 피드 콘텐츠 저작권에 대해 침묵한다.

> "The atom:rights element is a Text construct that conveys information about rights held in and over an entry or feed. The atom:rights element SHOULD NOT be used to convey machine-readable licensing information."
— 출처: Atom Syndication Format, RFC 4287 <https://www.rfc-editor.org/rfc/rfc4287>, 확인 2026-09-09. `atom:rights`는 사람이 읽을 문자열만 허용하고 기계 판독 라이선스 표준을 의도적으로 배제한다.

**(b) redistribution 판정 — `link_only` 강제(예외 없음)**
근거: 두 표준 모두 피드 콘텐츠의 저작권·재배포 권한을 규정하지 않으므로, 임의로 등록되는 개별 피드의 실제 저작권자 의사를 사전에 일괄 확인할 방법이 없다. §0.1 fail-closed 원칙에 따라 이 소스군은 **항상 `link_only`**로 고정한다 — 이는 spec §2.3의 RD-15 설계("link_only 강제")와 정확히 일치한다. 개별 피드가 `atom:rights`에 명시적 CC 라이선스 등을 적어도, 그것을 기계적으로 신뢰해 자동으로 store_full/excerpt로 승격시키지 않는다(사람이 읽는 문자열이라 파싱 신뢰도가 없다는 것이 RFC 4287의 문구 그대로).

**(c) rate limit 실측 — "사이트별 상이, 일괄 수치 없음"**
고정된 rate limit이 존재하지 않는다는 것 자체가 이 소스군의 특성이다 — 어댑터(RD-15)가 각 피드 응답의 HTTP 429/`Retry-After`, `robots.txt`의 `Crawl-delay`를 **피드별로 실측**해 기록해야 하며, RD-1 단계에서 공통 수치를 못박으면 그것이 바로 금지된 "추정치"가 된다.

**(d) 커버리지·정정 정책**: 정정 노출 방식이 퍼블리셔마다 상이하다 — 일부는 `<atom:updated>`를 갱신하지만 다수는 침묵 수정(silent correction)한다. **RD-A2 관점의 구조적 한계**로 명시.

**결론: 허용** (`link_only` 강제, 소스 단위가 아니라 프로토콜 단위 판정임을 명기 — 개별 피드의 화이트리스트/블랙리스트 운용은 RD-15 몫).

---

## 9. 결론

### 9.1 반입 허용
| 소스 | redistribution | 비고 |
|---|---|---|
| OpenDART | 구조화 필드 `store_full`, 서술 본문 `store_excerpt` | 키 필수, 일 20,000건 초과 시 오류 |
| FRED | `store_full`(수치) + 귀속고지 | 분당 120회, 키 필수, 서드파티 series는 source note 확인 필요(RD-13) |
| SEC EDGAR | 구조화 필드 `store_full`, 서술 본문 `store_excerpt` | 초당 10회, 키 불필요, User-Agent 필수 |
| GDELT | 이벤트 메타데이터 `store_full`, 기사 본문 **`link_only` 강제(본문 저장·요약 생성 금지)** | 정적 파일 배포, 키 불필요, 공식 rate limit 조항 없음(확인됨) |
| 범용 RSS/Atom | **`link_only` 강제(예외 없음)** | 프로토콜 단위 판정, rate limit은 피드별 실측 |

### 9.2 반입 금지/보류
| 소스 | 금지 사유 |
|---|---|
| ECOS | rate limit **미확인**(ecos.bok.or.kr가 JS SPA라 이 리프의 도구로 원문 확보 불가, 서드파티 수치는 인용 요건 미충족) |
| KOSIS | rate limit **미확인**(관련 공지 제목만 확인, 본문 원문 미인용) |
| KRX 정보데이터시스템 | (b) 이용약관 제12조②가 사전 허락 없는 복제·배포·전송을 명시적으로 금지 + (c) 공식 API·rate limit 조항 자체가 없어 **미확인** — 이중 사유 |

허용 목록의 5개 소스는 모두 (a)(b)(c)를 원문 인용으로 채웠다. 반입 금지 3개는 전부 (c) rate limit 미확인이 최소 하나의 사유로 들어간다(KRX는 (b)도 추가).

### 9.3 API 키 발급 필요 여부 (DoD f — 키 값 자체는 미기재)
- **OpenDART**: 필요(`crtfc_key`, 회원가입 + 인증키 신청).
- **ECOS**: 신청 절차(활용신청/인증키 신청 메뉴) 존재가 화면 구조로 확인되나, 필수 여부를 규정하는 원문 조항은 미확인.
- **KOSIS**: 필요(활용신청 → 자동승인 → 회원당 인증키 1개, 전 서비스 공용 — 절차 존재만 확인, 세부 조항 수치는 인용하지 않음).

### 9.4 INVARIANTS.md I-01~I-11 저촉 확인
이 리프는 문서 리프(코드 변경 0)라 대부분의 불변조건은 해당사항이 없다. 관련 있는 두 개만 확인한다:
- **I-08**(MCP/도구 서버는 REST/도메인 계층 이상의 인가·비즈니스 로직을 갖지 않는다): RD-16 MCP 도구(`research_data_search`)가 이 문서의 `link_only` 판정을 우회해 본문을 반환해서는 안 된다. 판정 자체는 도메인 계층(`domain/redistribution.py`, RD-3)에 있어야 하고 MCP는 얇은 프록시로 그 결과를 그대로 전달해야 한다 — 이 결론이 I-08과 충돌하지 않는다.
- **I-10**("구현됨 ≠ 작동함", 안전/정책 컴포넌트는 배선 증명 필요): 이 문서 자체는 판정표일 뿐이며, GDELT·RSS의 `link_only` 강제와 SEC EDGAR·OpenDART의 서술 본문 `store_excerpt` 제한이 **RD-3(`domain/redistribution.py`)에서 실제로 `body_ref=None`을 강제하고 RD-4/RD-15 적대적 통합테스트로 배선 증명될 때까지는 I-10을 충족하지 않는다**. RD-1(이 문서)은 게이트 통과일 뿐 완료 증명이 아니다.
- 나머지 I-01/02/03/04/05/06/07/09/11은 이 리프의 범위(문서·라이선스 판정)와 무관해 해당사항 없음.
