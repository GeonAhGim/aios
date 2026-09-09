# ADR-2026-09-09-A: IND-11 대상 패키지를 pandas-ta에서 pandas-ta-classic으로 교체

## Status
Accepted (2026-09-09, Chief Architect). esc-ind11-package-swap(2026-09-08) 결정.

## Context
IND-9 평가(`docs/design/INDICATOR_OSS_EVAL.md` §6)가 명세 원안 `pandas-ta 0.4.71b0`을 런타임·감사 축에서 탈락시켰다:
Python>=3.12 요구(현 venv 3.10.11에 설치 불가), 공개 저장소 404(코드 감사 불가), `numba==0.61.2` 고정.
대체 후보 `pandas-ta-classic 0.6.52`: MIT(원저작 귀속 유지), 공개 저장소, Python>=3.10, 경량 의존성, TA-Lib 오라클 테스트,
255 모듈로 IND-11 DoD(>=100종) 충족. 평가 점수 88 대 68.

## Decision
IND-11의 대상 패키지를 `pandas-ta-classic`으로 바꾼다. 명세 §9.9 IND-11 행을 갱신한다.
DoD에 `pip show tulipy`가 not found임을 추가한다(oracle extra 미설치 — tulipy는 LGPL이라 반입 금지, 평가 문서 §5).
ADR-2026-09-06-F 규칙(TA-Lib과 겹치는 지표 제외, 순증분만 등록)은 유지한다.

## Rejected
- pandas-ta 원안 유지 + venv를 3.12로 올리기: 저장소 404라 감사 불가한 점은 해소되지 않는다.
- IND-11 폐기: TA-Lib 161종 밖의 순증분(커스텀·최신 지표)을 잃는다. 비용이 작고 이득이 분명하다.
