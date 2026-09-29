# Postmortem 초안: human_blocked_ops_deploy·backup_drill_failed 오늘 사고

- 생성 시각: 2026-09-29T08:34:42+00:00
- 구간: 2026-09-29T05:00:00+00:00 이후, 이벤트 4건
- 상태: 초안(자동 생성) — 사람 검토·보완 필요

## 영향

- 새 소견 2건 오픈: backup_drill_failed, human_blocked_ops_deploy
- 자동 조치 1건(triage_failed)

## 타임라인

- 2026-09-29 14:27:33 `finding_open` code=backup_drill_failed, severity=high, detail=복구 리허설 실패(2026-09-29T05:27:33+00:00) — error: timeout 1200s
- 2026-09-29 17:11:19 `finding_open` code=human_blocked_ops_deploy, severity=high, detail=C:/aios/pm 워킹트리가 origin/main보다 6커밋 뒤처진 상태가 26분째다 — push는 됐지만 상주 프로세스에는 배포되지 않았다
- 2026-09-29 17:16:15 `remediate` code=human_blocked_ops_deploy, action=triage_failed, detail=자동 분류 실패(qwen3.6-35b-a3b, confidence=low) — 사람 확인 필요
- 2026-09-29 17:24:10 `nd_decision` rule=ND-29, result=human_blocked_ops_deploy 조치 리프 6건 이미 발행(>=6) — 같은 접근 반복 대신 CA 결정으로 전환, entity_id=None, code=human_blocked_ops_deploy

## 추정 원인

- 잠금 회수 관련: finding_open human_blocked_ops_deploy
- 잠금 회수 관련: remediate human_blocked_ops_deploy
- 잠금 회수 관련: nd_decision human_blocked_ops_deploy

## 후속 조치(사람이 채운다)

- [ ] 
