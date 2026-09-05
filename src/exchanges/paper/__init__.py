"""PAPER 시뮬레이터 체결 모델(L4 명세 §2-F).

Spec: docs/specs/L4_execution_oms_and_exchange_v1.0.md §2-F, §9 L4-22.

이 패키지의 모델 4종(fill/fee/latency/venue_profile)은 **순수 도메인**이다 —
I/O 없음, 전역 난수·전역 시계 사용 금지, 모든 무작위성·지연은 호출자가
주입한다. `simulator_adapter`/`ledger_repository`(L4-23)가 유일한 소비자이며
`is_paper_trading`/`is_sandboxed` 상수도 그쪽 책임이다(R11/R12 — 이 리프는
LIVE 하드 가드를 건드리지 않는다).
"""
