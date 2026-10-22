# 파일 범위 분할 제안: task-16648

## 원본 작업 분석

- **task-16648**: QA(묶음 1건) — CI complexity 정정
- **원본 files**: `["src/"]` — 전체 소스 디렉토리 (너무 광범위)
- **문제**: CI complexity 단계에서 `over_cap_count 9개 -> 10개 (증가, CAP=25)` 보고
- **현재 상태**: 로컬 게이트 녹색 (9=9, baseline 일치). 이전 커밋 `7740471bd`에서 이미 정정됨

## 실제 게이트 대상 파일

`check_complexity.py`가 스캔하는 대상:
1. `src/` 내 모든 `.py` 파일 (1569개 파일, 10개 함수가 CAP=25 초과)
2. `scripts/` 내 모든 `.py` 파일

현재 CAP=25 초과 함수 9개:
| 파일 | 함수 | 복잡도 |
|------|------|--------|
| `src/core/script/import_/pine/lexer.py` | `tokenize()` | 74 |
| `src/core/script/grammar/lexer.py` | `tokenize()` | 64 |
| `src/foundation/backtest/application/quick_backtest.py` | `run_quick_backtest()` | 63 |
| `scripts/consistency/wiring.py` | `check_port_protocol_implementations()` | 46 |
| `scripts/check_zone_manifest.py` | `main()` | 30 |
| `src/core/indicators/engine/__init__.py` | `run_chain_incremental()` | 27 |
| `src/foundation/backtest/application/run_backtest.py` | `run_backtest()` | 27 |
| `src/foundation/mandates/application/activate_revision.py` | `activate_revision()` | 27 |
| `src/foundation/market_data/domain/instruments/roll.py` | `continuous_futures()` | 27 |

## 분할안

### 분할안 A: scripts 게이트 인프라 (2 함수)
- **파일**: `scripts/check_complexity.py`, `scripts/consistency/wiring.py`
- **범위**: 게이트 스크립트 자체의 복잡도 개선
- **리프 수**: 2개 (각 함수당 1개)
- **의도**: 인프라 함수의 복잡도를 낮춰 게이트 신뢰성 향상

### 분할안 B: core 도메인 (3 함수)
- **파일**: `src/core/script/import_/pine/lexer.py`, `src/core/script/grammar/lexer.py`, `src/core/indicators/engine/__init__.py`
- **범위**: 핵심 도메인 로직의 복잡도 개선
- **리프 수**: 3개 (각 함수당 1개)
- **의도**: 핵심 모듈의 유지보수성 향상

### 분할안 C: foundation 도메인 (4 함수)
- **파일**: `src/foundation/backtest/application/quick_backtest.py`, `src/foundation/backtest/application/run_backtest.py`, `src/foundation/mandates/application/activate_revision.py`, `src/foundation/market_data/domain/instruments/roll.py`
- **범위**: 파운데이션 도메인 로직의 복잡도 개선
- **리프 수**: 4개 (각 함수당 1개)
- **의도**: 파운데이션 모듈의 유지보수성 향상

## 권장

**분할안 B → A → C 순서**로 권장. core 도메인이 가장 중요하고, scripts 게이트 인프라는 핵심 로직보다 영향 범위가 좁음.

## 비고

- 현재 CI complexity는 녹색(9=9). 이전 회귀(`7740471bd`)가 이미 정정됨.
- task-16648의 실제 목적(회귀 정정)은 이미 완료되었으므로, 새로운 분할 작업은 **예방적 복잡도 개선**으로 재정의 필요.
- 원본 hold 해제·재큐·코드 수정은 금지 (task-16651 spec).
