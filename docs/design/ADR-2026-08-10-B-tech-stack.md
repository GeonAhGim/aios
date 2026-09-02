# ADR-2026-08-10-B: 기술 스택 확정 (백엔드·DB·인증·프론트엔드)

## Status
Accepted (2026-08-10, 사용자 승인)

## Context
00번 문서 §0.2 "기술 스택 가정(확인 필요)" 표가 Python 3.11+/Pydantic v2/PostgreSQL/
SQLAlchemy 2.0(선택)/asyncio를 Draft로만 가정하고 있었고, "실제 팀 스택 결정 시
이 표를 업데이트하고 ADR로 기록한다"고 명시돼 있었으나 지금까지 확정된 적이 없다.
또한 15번 API 스펙(FD-11~21)이 이후에 추가되면서 실제 웹 프레임워크가 무엇인지는
그 표에 아예 없었다 — API 엔드포인트만 나열되고 이를 구현할 프레임워크가 없는 상태로
남아있었다.

FD-11~21의 클래스/함수 시그니처 작성에 착수하기 전, 이 공백을 먼저 메운다.

## Decision

| 항목 | 확정값 | 비고 |
|---|---|---|
| 언어 | Python 3.11+ | 00번 기존 가정 유지 |
| 웹 프레임워크 | **FastAPI** | 신규 확정 — Pydantic v2 네이티브 통합, 비동기 지원,
  OpenAPI 스펙 자동생성(15번 문서 수동 관리 부담 경감) |
| 데이터 모델 | Pydantic v2 | 00번 기존 가정 유지 |
| DB | **PostgreSQL** | 00번 Draft → 확정. DDL(04번 문서)이 이미 Postgres 문법
  (JSONB, gen_random_uuid() 등) 전제로 작성돼 있었음 |
| ORM | **SQLAlchemy 2.0 (async)** | 00번 Draft "(선택)" → 확정 |
| DB 드라이버 | **asyncpg** | SQLAlchemy async + PostgreSQL 표준 조합 |
| 인증 토큰 | **JWT (PyJWT)** + FastAPI `OAuth2PasswordBearer` 패턴 | FD-11.1의
  "Draft: JWT" → 확정 |
| 비동기 처리 | asyncio + httpx(REST)/websockets(WS) | 00번 기존 가정 유지 |
| 프론트엔드(웹) | **React + TypeScript** | 신규 확정 — 모바일(FD-21, React Native)과
  비즈니스 로직(API 클라이언트, 상태관리 hooks) 공유 목적 |
| 프론트엔드 상태관리 | (착수 시 확정, Draft 후보: TanStack Query + Zustand) | 서버
  상태(API 데이터)와 클라이언트 상태를 분리 관리하는 통상 패턴 |

## Rejected Alternatives
- **Django REST Framework**: 동기 우선 아키텍처라 FD-9(Watchdog 등 비동기 상시
  감시)와의 통합이 FastAPI보다 부자연스러움 — 기각
- **Flask**: 비동기·OpenAPI 자동생성이 기본 제공이 아니라 별도 확장 필요 — 기각

## Impact
- 00번 문서 §0.2 표 갱신 필요(Draft → 확정, 웹 프레임워크 행 추가) — 별도 패치로
  반영
- FD-11~21 클래스/함수 시그니처는 이제 FastAPI 라우터(`APIRouter`) + Pydantic
  요청/응답 모델 + SQLAlchemy async 세션 주입(`Depends`) 패턴으로 작성
- 15번 API 스펙 문서는 향후 FastAPI의 자동 OpenAPI 생성(`/docs`)과 병행 —
  손으로 관리하는 15번 문서가 진실의 원천이 아니게 되는 시점부터는(실제 코드
  착수 이후) 코드의 OpenAPI 스펙이 우선

## References
- 00_overview-2.md §0.2
- FD-11.1(JWT Draft 표기)
- 04번 DB스키마(Postgres DDL 문법 기존 전제)
