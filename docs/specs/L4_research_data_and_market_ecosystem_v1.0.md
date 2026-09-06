# L4 리서치 데이터 플레인(공시·뉴스·거시·대안 데이터) 명세 v1.0

## 0. 문서 메타
- status: Accepted (2026-09-06) — ADR-2026-09-06-A D2·D3의 실행 명세
- owner role: Chief Architect(원칙·벤더 승인), PM(리프 배정)
- depends on: DC-1~9(심볼 마스터·커버리지·권한), DC-19~22(point-in-time 참조 데이터), PLT §3(에러 봉투·테넌시),
  AI-4/AI-14(에이전트 토큰·research 도구), L0-3(WORM)
- implemented by: `src/foundation/research_data/**`, `src/api/routers/research_data.py`, `frontend/apps/web/src/research/**`
- 리프 접두: **RD**
- 원칙: **포트 먼저, 소스는 어댑터로 나중에.** 새 데이터 소스 추가 = 어댑터 1파일 + 평가 리프 1개. 코어 계약 변경 없음.

## 1. 기관급 요구
| 요구 | 내용 | 강제 지점 |
|---|---|---|
| 시점 정합(누수 차단) | 모든 항목은 `known_at`(그 사실을 알 수 있었던 시각)을 갖고, 백테스트·전략은 `known_at <= bar_ts`만 읽는다 | RD-2, RD-9 |
| 출처 추적 | 원문 URL·발행자·수집 시각·해시를 보존, 본문은 라이선스가 허용할 때만 저장 | RD-2, RD-3 |
| 라이선스 강제 | 소스별 `redistribution` 정책(store_full / store_excerpt / link_only)을 메타에 박고 코드가 강제 | RD-3 |
| 정정 이력 | 공시 정정·기사 수정은 새 행 append(수정 금지), 이전 판본 조회 가능 | RD-2, RD-6 |
| 권한 | 테넌트별 소스 접근은 DC-9 entitlement 정책을 그대로 사용 | RD-8 |
| 엔티티 연결 | 항목 ↔ `instrument_id` 매핑(종목코드·법인번호·티커 인식), 미매핑은 미매핑으로 남긴다(추측 금지) | RD-5 |

## 2. 모듈 분해 (파일 ≤300줄)
### 2.1 계약·도메인
| 파일 | 책임 |
|---|---|
| `contracts/v1.py` | `ResearchItem{item_id, source_id, kind: filing\|news\|macro\|alt, published_at, known_at, instruments: tuple[str,...], title, body_ref\|None, url, language, hash, revision_of\|None}`, `SourceMeta{source_id, publisher, redistribution: store_full\|store_excerpt\|link_only, license_ref, rate_limit, coverage}` |
| `domain/known_at.py` | `known_at` 산정 규칙(발행 시각·공시 접수 시각·지연 규정) + `assert_point_in_time(item, as_of)` (순수) |
| `domain/redistribution.py` | 소스 정책에 따른 저장 허용 범위 판정(본문 저장 금지 소스는 `body_ref=None` 강제) |
| `domain/entity_link.py` | 종목코드/법인등록번호/티커/ISIN → `instrument_id` 매핑(순수, 미매핑 허용) |
| `domain/revision.py` | 정정 판본 체인(원본 ↔ 정정) 규칙 |
| `domain/macro_series.py` | 거시 시계열 정규화(빈도·단위·계절조정 플래그·발표 지연) |

### 2.2 포트·저장
| 파일 | 책임 |
|---|---|
| `ports/{news_provider,filing_provider,macro_provider,alt_data_provider}.py` | 4종 SPI Protocol. 공통: `capabilities()`, `fetch(span, cursor)`, `stream()`(선택) |
| `ports/research_repository.py` | 저장 포트 |
| `adapters/postgres_repository.py` + 마이그레이션 | `research_items`(append-only, WORM), `research_sources`, `research_item_instruments` |
| `adapters/object_body_store.py` | 본문 저장(허용 소스만), 해시 주소화 |

### 2.3 어댑터 (소스별 — 계층 A부터)
| 파일 | 소스 | 비고 |
|---|---|---|
| `adapters/sources/opendart.py` | 금감원 OpenDART | 공시 목록·재무제표·정정 공시. 국내 1순위 |
| `adapters/sources/ecos.py` | 한국은행 ECOS | 금리·환율·통화량 등 거시 |
| `adapters/sources/kosis.py` | 통계청 KOSIS | 물가·고용·산업활동 |
| `adapters/sources/krx_data.py` | KRX 정보데이터시스템 | 지수·공매도·투자자별 매매 |
| `adapters/sources/fred.py` | 미 연준 FRED | 글로벌 거시 |
| `adapters/sources/sec_edgar.py` | SEC EDGAR | 미국 공시(10-K/Q, 8-K) |
| `adapters/sources/gdelt.py` | GDELT | 글로벌 뉴스 이벤트(링크 전용) |
| `adapters/sources/rss_generic.py` | 범용 RSS/Atom | 사용자가 소스 추가 가능 |

### 2.4 응용·API
| 파일 | 책임 |
|---|---|
| `application/{ingest_job,backfill_job}.py` | 수집(멱등·재개·rate limit)·과거 적재 |
| `application/query.py` | `search(instruments, kinds, span, as_of)` — `as_of` 미지정 시 현재, 지정 시 point-in-time |
| `application/link_entities.py` | 미매핑 항목 재매핑 배치 |
| `src/api/routers/research_data.py` | 검색·소스 목록·수집 상태(인간 세션) |
| MCP 도구 `tools_research.research_data_search` | 에이전트용(읽기 전용, entitlement 경유) |

## 3. 계약 (요지)
- `known_at` 없는 항목은 저장 거부(400). 백테스트 경로 조회는 `as_of` 필수.
- `redistribution=link_only` 소스는 `body_ref`가 항상 `None`이며, 저장 시도는 `RD_REDISTRIBUTION_DENIED`(403).
- 에러: `RD_SOURCE_UNAVAILABLE`(503), `RD_RATE_LIMITED`(429, retry_after), `RD_POINT_IN_TIME_VIOLATION`(409),
  `RD_REDISTRIBUTION_DENIED`(403), `RD_ENTITY_UNMAPPED`(정상 응답에 플래그, 에러 아님).
- 멱등: `(source_id, external_id, revision)` 유일. 재수집은 같은 행.

## 4. 불변조건
- **RD-A1** `known_at > as_of`인 항목은 어떤 조회 경로로도 반환되지 않는다(적대적 테스트 필수).
- **RD-A2** `research_items`는 append-only(UPDATE/DELETE 트리거 거부). 정정은 `revision_of`로 새 행.
- **RD-A3** 소스 라이선스 정책은 코드가 강제한다(문서 주석이 아님).
- **RD-A4** 엔티티 매핑은 확정적 키(종목코드·법인번호·ISIN·티커+거래소)만 사용. 이름 유사도 추측 매핑 금지.

## 5. 동시성·멱등성
- 수집 잡: `(source_id, span)` advisory lock, 커서 조건부 UPDATE, 재개 가능. 저장: append-only + 유일키 충돌 무시(멱등).

## 6. 실패 모드
| 실패 | 조치 |
|---|---|
| 소스 장애·rate limit | backoff, 커버리지에 갭 표시(조용한 결측 금지) |
| 정정 공시 도착 | 새 행 + `revision_of`, 기존 결과 무효화 알림 |
| 매핑 실패 | 미매핑 플래그 유지, 배치 재매핑, 추측 금지 |
| 라이선스 정책 변경 | 소스 메타 갱신 → 기존 본문 저장분 격리 잡 |

## 7. SLO
- 검색 p95 400ms(10만 항목 기준), 수집 잡 지연 ≤ 소스 발표 + 5분(공시), 백필 재개 가능.

## 8. 테스트
- 적대적: 미래 항목 조회 차단(RD-A1), link_only 소스 본문 저장 시도, append-only 위반, 교차 테넌트 조회, 이름 기반 추측 매핑.
- 계약: 소스별 정규화 스냅샷(픽스처), 정정 체인 왕복.

## 9. 리프 목록 (구현 순서)
| 리프 | 파일 | 선행 | DoD | 크기 |
|---|---|---|---|---|
| RD-1 | `docs/design/RESEARCH_DATA_SOURCE_EVAL.md` — 계층 A 8개 소스의 **이용약관·라이선스 원문 인용** + 커버리지·정정 정책·rate limit 실측 채점표(CH-0 형식) | — | 소스별 `redistribution` 판정 근거 명시. 미확인 소스는 반입 금지 | 260 |
| RD-2 | `contracts/v1.py` + `domain/known_at.py` + test | — | 스키마 스냅샷, `known_at` 없는 항목 거부, PIT 판정 | 500 |
| RD-3 | `domain/redistribution.py` + `domain/revision.py` + test | RD-2 | link_only 본문 강제 None, 정정 체인 | 400 |
| RD-4 | 마이그레이션(`research_items` WORM·`research_sources`·매핑 테이블) + `adapters/postgres_repository.py` + 통합 | RD-2 | append-only 증명, 멱등 유일키 | 560 |
| RD-5 | `domain/entity_link.py` + `application/link_entities.py` + test | RD-2 | 확정 키만 매핑, 추측 거부 | 400 |
| RD-6 | `application/{ingest_job,backfill_job}.py` + 통합 | RD-4 | 중단·재개, rate limit backoff, 갭 표시 | 500 |
| RD-7 | `application/query.py` + `as_of` PIT 필터 + 적대적 테스트 | RD-4 | RD-A1 위반 0 | 300 |
| RD-8 | entitlement 연동(DC-9) + `src/api/routers/research_data.py` + 통합 | RD-7, DC-9 | 교차 테넌트 404, 소스 권한 403 | 400 |
| RD-9 | 백테스트·DSL 연결: `research.*` 조회를 스크립트에 노출하되 `as_of` 자동 바인딩 + 누수 테스트 | RD-7, DSL-9 | 미래 참조 시 컴파일/런타임 거부 | 300 |
| RD-10 | `adapters/sources/opendart.py` + 계약 테스트(픽스처) | RD-1, RD-6 | 공시 목록·재무·정정 정규화 | 300 |
| RD-11 | `adapters/sources/{ecos,kosis}.py` + `domain/macro_series.py` + test | RD-1, RD-6 | 빈도·단위·발표지연 정규화 | 460 |
| RD-12 | `adapters/sources/krx_data.py` + test | RD-1, RD-6 | 지수·투자자별·공매도 | 300 |
| RD-13 | `adapters/sources/fred.py` + test | RD-1, RD-6 | 거시 시계열 | 240 |
| RD-14 | `adapters/sources/sec_edgar.py` + test | RD-1, RD-6 | 10-K/Q·8-K 정규화 | 300 |
| RD-15 | `adapters/sources/{gdelt,rss_generic}.py` + test | RD-1, RD-3 | link_only 강제, 사용자 RSS 등록 | 400 |
| RD-16 | MCP `research_data_search` 도구(얇은 프록시) + 적대적 테스트 | RD-8, AI-15 | I-08, PIT 유지 | 240 |
| RD-17 | 프론트 `ResearchPage.tsx`(검색·소스 상태·종목 연결 표시) | RD-8 | 화면·negative | 300 |
| RD-18 | 커버리지·신선도 대시보드 패널 + 알림(수집 지연·소스 장애) | RD-6, PLT-10 | 지연 알림 동작 | 260 |

## 10. 미확정·리스크
- 계층 B(Databento·Polygon·EODHD·IBKR) 어댑터는 계약 체결 후 별도 리프(RD-19~). 이용약관은 RD-1과 같은 형식으로 확인한다.
- 뉴스 본문 저장은 대부분 금지된다고 보고 설계했다 — 요약·임베딩 생성도 라이선스 확인 전에는 금지.
- 국내 소스 API 키(OpenDART·ECOS·KOSIS)는 사용자 발급 필요. 키 없으면 어댑터는 명시적으로 비활성(무음 실패 금지).
