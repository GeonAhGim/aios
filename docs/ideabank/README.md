# AIOS Idea Bank

AIOS 개발 과정에서 대화·검토를 통해 구체화되는 제품, 아키텍처, 비즈니스 모델, 사용자 경험, 마켓플레이스, Creator Economy, 결제·정산·AML, 커뮤니티·SNS·온라인 방송 아이디어를 보존하는 별도 아이디어 저장소.

이 저장소는 `GeonAhGim/aios`의 구현 저장소와 역할을 분리한다.

- `aios`: 실제 구현, 명세, ADR, 테스트, 운영 코드
- `aios-idea-bank`: 아직 확정되지 않았거나 추가 검토가 필요한 제품/사업/생태계 아이디어를 훼손하지 않고 축적
- Idea Bank의 내용은 자동으로 AIOS의 Architecture Decision이 되지 않는다.
- 구현 채택 시 별도 ADR/Spec/Task로 승격해야 한다.
- 아이디어 단계의 규제·회계·법률 가정은 실제 상용화 전에 전문가 검토가 필요하다.

## 기록 원칙

1. 아이디어를 지나치게 요약하지 않는다.
2. 아이디어가 나온 배경, 문제의식, 사용자 관점, 기술적 이유, 반대 논리와 위험까지 함께 남긴다.
3. `IDEA → REVIEW → ACCEPTED → ADR/SPEC`의 승격 경로를 사용한다.
4. AIOS 구현 저장소의 확정 아키텍처와 아이디어를 혼동하지 않는다.
5. 금융 안전·리스크·권한·정산과 관련된 아이디어는 fail-closed를 기본값으로 검토한다.

## 2026-09-10 기록

- [01. 개발단계에 따른 구현정책 진화](2026-09-10/01-development-policy-evolution.md)
- [02. 사용자 경험과 BYOAI Agent Connector](2026-09-10/02-user-experience-and-byoai.md)
- [03. Marketplace 제품 개념과 사용자 여정](2026-09-10/03-marketplace-product-concept.md)
- [04. Marketplace 신뢰·검증·버전·평판](2026-09-10/04-marketplace-trust-verification.md)
- [05. 구독결제·AIOS Fee·Creator 정산·AML](2026-09-10/05-payments-settlement-aml.md)
- [06. Community·SNS·Live Broadcast·Creator Economy](2026-09-10/06-social-community-broadcast.md)
- [07. 후속 숙제 / 아직 확정하지 않은 문제](2026-09-10/07-open-homework.md)

## 현재 핵심 방향

AIOS의 최종 사용자 경험은 단순 자동매매 프로그램이 아니라 다음 요소가 하나의 데이터·권한·검증·원장을 공유하는 금융 운영 플랫폼으로 발전하는 방향을 검토한다.

`Trading OS + BYOAI Agent Platform + Strategy Marketplace + Financial Social Network + Creator Platform`

단, 이 문구 자체도 제품 정의의 최종 확정문은 아니다. Idea Bank에서 검증 후 Architecture/Product Constitution으로 승격한다.
