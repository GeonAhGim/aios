# MCP 도입 효과 감사 — OPS-40(task-3067), ADR-2026-09-10-B

## 방법론
- 소스: `C:\aios\pm\logs\mcp_metrics_history.jsonl`(worker_runner._append_mcp_metrics_history가
  매 워커 실행마다 남기는 append-only 로그 — role/붙은 MCP 이름/OPS-29 metrics(턴·duration_ms·
  cache_read)·status·시각) + `C:\aios\pm\mcp\rollout_log.json`(레인별 MCP 도입 시각).
- 비교 단위는 **레인**(backend/frontend/qa)이다 — 한 레인의 MCP들이 같은 커밋에서 한꺼번에
  붙으므로 개별 MCP 단위로는 24h 전후 표본을 분리할 수 없다(순차 롤아웃을 하지 않는 한).
- 재생성: `python C:\aios\pm\mcp\effect_report.py --write` (마커
  `<!-- OPS-40:TABLE:START/END -->` 사이만 갱신, 이 파일의 나머지 내용은 손대지 않는다).
- 판정 기준: 도입 후 24h 표본이 쌓이면 턴·duration_ms·cache_read 평균이 도입 전보다 개선되고
  실패율(blocked/needs_decision 비율)이 늘지 않아야 keep. 표본이 있는데 개선이 없으면 그
  레인에서 해당 MCP를 mcp/lane_servers.py의 LANES에서 빼고(→ mcp/gen_lane_configs.py 재생성)
  사유를 이 문서에 기록한 뒤 remove 처리한다(ADR-2026-09-10-B "개선이 없으면 되돌린다").

## 비교 표
이 task(3067) 커밋 시점에 막 도입돼 아직 24h가 지나지 않았다 — 아래는 전부 "측정 대기"다.
가짜 0을 개선으로 보고하지 않는다(CLAUDE.md warn_baselines 관례와 같은 정신: 정직하게
`baseline_measured` 안 된 상태를 남긴다). 24h 뒤 `effect_report.py --write`로 갱신하는
후속 task가 실측치를 채운다.

<!-- OPS-40:TABLE:START -->
| 레인 | 도입 MCP | 도입 시각(UTC) | 도입 전 24h | 도입 후 24h | 판정 |
|---|---|---|---|---|---|
| backend | aios-lsp, aios-context7 | 2026-09-10T08:23:16+00:00 | 측정 대기(표본 0) | 측정 대기(표본 0) | 측정 대기 |
| frontend | aios-lsp, aios-playwright | 2026-09-10T08:23:16+00:00 | 측정 대기(표본 0) | 측정 대기(표본 0) | 측정 대기 |
| qa | aios-playwright, aios-postgres-ro | 2026-09-10T08:23:16+00:00 | 측정 대기(표본 0) | 측정 대기(표본 0) | 측정 대기 |
<!-- OPS-40:TABLE:END -->

## 레인 구성 (mcp/lane_servers.py, 레인당 최대 3개)
| 레인 | MCP | 비고 |
|---|---|---|
| backend | review-context, lsp(pyright), context7 | |
| frontend | review-context, lsp(tsserver), playwright | |
| qa | review-context, playwright, postgres-ro | |
| ops | review-context | 이번 task로 추가된 MCP 없음(비교 대상 아님) |

task 제목의 "Context7 전 레인"·"Postgres backend/QA"는 레인당 3개 상한(ADR-2026-09-10-B)을
넘겨서, task-3067 spec 본문의 명시적 레인 구성표(위 표)를 그대로 구현했다.

## fail-open
서버 기동 실패(바이너리 없음·타임아웃·프로토콜 불일치)는 `mcp/health.py`가 감지해 그 서버만
레인 config에서 빼고 `--mcp-config` 없이(또는 나머지만으로) 진행한다 — 로그는
`logs/<worker>-<task>.log` 맨 앞줄, 계측은 `mcp_metrics_history.jsonl`의 `mcp_attached`
목록(빠진 서버는 여기 없다)으로 남는다.
