# Codex 전수감사 의견서 검토 기록

외부(Codex) 전수감사의 지적을 **한 줄 = 한 지적**으로 기록하고, 검증·수정·종결까지 추적한다.
이 폴더의 `findings.jsonl`이 정본이다. 코드는 `scripts/audit_review/`에 있다.

> 검토는 MVP-1 종결 이후에 시작한다(사용자 결정 2026-11-17). 지금은 수집·증거 검색 골격만 있고
> 모든 지적이 `DISCOVERED` 상태다. 판정·분류 필드는 비어 있다.

## 상태 흐름

`DISCOVERED → TRIAGED → VERIFIED → CONFIRMED → FIXED → CLOSED`, 그리고 `VERIFIED → REJECTED`.

| 상태 | 뜻 | 필수 조건 |
|---|---|---|
| DISCOVERED | 수집됨 | - |
| TRIAGED | 분류함(category) | - |
| VERIFIED | 코드로 검증함 | `verdict` + 증거(파일·줄·**코드 원문 인용**) |
| CONFIRMED | 결함 확정 | `severity` + `priority`(A~E) |
| FIXED | 수정 리프 병합 | `fix_task`(태스크 번호) |
| CLOSED | 병합·CI 통과로 종결 | - |
| REJECTED | 결함 아님(설계 의도·이미 수정·중복) | VERIFIED와 같은 증거 |

- 한 번에 한 단계씩만 나아가고 되돌리지 않는다(REJECTED는 VERIFIED에서만).
- TESTED·DEPLOYED는 두지 않는다. 태스크와 CI가 이미 추적한다.
- `priority`: A 출시 차단급 · B 사용성 저해 · C 운영 리스크 · D 성능 · E 단순 개선. **A/B/C만 먼저 처리한다**(범위 폭주 방지).
- `category`: money, order, auth, data, ops, perf, ui, other.

## 증거 규칙(환각 방지)

VERIFIED 이후 상태는 증거 인용이 **실제 저장소 파일에 그대로 있어야** 한다. 스크립트가 기계로 대조하고,
코드가 바뀌어 인용이 사라지면 `--check`가 그 기록을 위반으로 알린다(증거가 낡았다는 신호).

로컬 LLM(qwen 35B)의 1차 분류는 오판이 있었다(2026-11-17: A-16은 설계 의도, A-20은 그대로 유효인데 둘 다
"이미 수정"으로 판정). **로컬 LLM 판정은 라벨로 쓰지 않는다.** 증거 검색(요약·후보 찾기)에만 쓴다.

## 사용

모두 저장소 루트에서 `python -m scripts.audit_review.<모듈>`로 실행한다.

```bash
# 1) 수집 - Codex 폴더에서 새 지적만 증분으로 합친다(기존 검토 필드는 덮어쓰지 않는다)
python -m scripts.audit_review.ingest --src <Codex 체크포인트 폴더>

# 2) 증거 후보 - 지적 본문의 식별자로 저장소를 검색해 관련 코드를 보여 준다(정의 파일이 먼저)
python -m scripts.audit_review.evidence 005 A-20

# 3) 기록 - 규칙을 통과해야만 파일에 쓴다
python -m scripts.audit_review.status set 005 A-20 --state TRIAGED --category order
python -m scripts.audit_review.status set 005 A-20 --state VERIFIED --verdict still_valid \
    --evidence "src/services/execution_loop/pending_fill.py|59|if order is None or order[\"exchange_order_id\"] is None:"

# 4) 현황 / 규칙 점검
python -m scripts.audit_review.status
python -m scripts.audit_review.status --check
```

## 설계 원칙

- 새 서비스·DB를 만들지 않는다. 파일과 git 이력이 감사 기록이다.
- 검증(판정)은 사람이나 Claude가 한다. 도구는 형식·전이·증거의 실재만 기계로 확인한다.
- 정정은 기존 경로로 한다: 유효로 확정된 지적만 정정 리프를 발행하고, 그 번호를 `fix_task`에 적는다.
- 같은 (체크포인트, 지적 ID)는 한 번만 들어온다. Codex가 원문을 고치면 검토된 기록에는 `source_changed`만 표시한다.
