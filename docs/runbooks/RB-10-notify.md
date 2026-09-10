# RB-10: 알림 경로(Alertmanager -> 웹훅) 점검

Spec: `docs/design/ADR-2026-09-09-B-mvp1-hardening-and-mvp2-scope.md` H-10,
`config/observability/alert_rules.yaml`, `config/observability/alertmanager.yml`,
`src/core/observability/notify_port.py`, `scripts/observability/notify_selftest.py`.

## 트리거 알림

- `notify_selftest_missing` / `notify_selftest_unreadable` / `notify_selftest_failed` /
  `notify_selftest_stale`(전부 high, `C:\aios\pm\healthcheck.py`의
  `check_notify_selftest()`) -- 24시간 안에 성공한 알림 경로 self-test가 없다는 뜻이다.
  이 자체는 "실제 장애"가 아니라 "alert_rules.yaml의 11개 규칙이 사람에게 닿는다는
  증거가 없다"는 신호다(H-10의 문제 정의: "alert_rules 11건이 사람을 호출하지 않음").

## 배경

세 조각이 사슬을 이룬다:

```
alert_rules.yaml(평가) -> alertmanager.yml(라우팅) -> notify_port.py(발신) -> Slack
```

- `config/observability/alert_rules.yaml` -- A1~A11 규칙, 각각 `severity`(warn|critical)와
  `runbook` 라벨을 가진다.
- `config/observability/alertmanager.yml` -- severity로 라우팅해 `slack_configs` 수신자로
  보낸다. `api_url`은 배포 시 `.env`의 `ALERT_WEBHOOK_URL`로 치환된다(H-6 컨테이너 기동
  범위). Alertmanager 서비스 자체를 아직 배포하지 않았어도(H-6 미완료), 이 라우팅 규칙은
  붙는 즉시 쓸 수 있게 미리 정의돼 있다.
- `src/core/observability/notify_port.py` -- `WebhookNotifyAdapter`가 같은
  `ALERT_WEBHOOK_URL`로 Slack 호환 페이로드(`{"text": ...}`)를 직접 발신한다. 이건
  Alertmanager와 별개 경로로, self-test와(추후 앱 내부에서 트리거하는 알림에) 쓰인다.

## 정기 self-test(일 1회)

1. `python -m scripts.observability.notify_selftest` -- 저장소 루트에서 모듈 형태로
   실행한다(직접 실행하면 `scripts/`가 `sys.path[0]`이 되어 import가 깨진다).
2. `ALERT_WEBHOOK_URL`이 `.env`에 설정돼 있어야 한다 -- 비어 있으면 self-test는
   `ok=False`, `error="ALERT_WEBHOOK_URL not configured"`로 실패한다(성공한 척 위장하지
   않는다 -- 이게 H-10이 막으려는 바로 그 사고다).
3. 결과는 `runtime/notify/selftest_latest.json`(로컬)과
   `C:\aios\pm\notify\selftest_latest.json`(fleet healthcheck용) 두 곳에 남는다.
   cron/스케줄러로 하루 1회 돌리는 것이 전제다(`check_notify_selftest()`의 24시간
   기준과 맞춰야 한다).

## self-test가 실패했을 때

1. `C:\aios\pm\notify\selftest_latest.json`(또는 로컬
   `runtime/notify/selftest_latest.json`)의 `error`/`status_code`를 본다:
   - `error`가 `"ALERT_WEBHOOK_URL not configured"`다 -- `.env`에 슬롯이 비어 있다.
     Slack에서 Incoming Webhook을 발급해 채운다.
   - `status_code`가 4xx/5xx다 -- 웹훅 URL이 폐기됐거나(Slack 앱 삭제/재발급),
     Slack 쪽 레이트리밋일 수 있다. Slack 앱 설정에서 웹훅이 살아있는지 확인한다.
   - `error`에 네트워크 예외 문자열이 있다(타임아웃 등) -- 아웃바운드 네트워크
     차단(방화벽/프록시)을 의심한다.
2. 원인을 고치고 `python -m scripts.observability.notify_selftest`를 수동으로 다시
   돌려 다음 healthcheck 폴링 전에 `notify_selftest_*` finding이 해소되는지 확인한다.

## 웹훅 수신 전달 테스트(개발 검증용)

`tests/unit/core/observability/test_notify_port.py`가 `httpx.MockTransport`로 웹훅
수신 서버를 모킹해, `WebhookNotifyAdapter.send()`가 보내는 페이로드(`text` 필드에
alertname·severity·summary·runbook 포함)와 URL이 기대와 일치하는지, 4xx/5xx·네트워크
예외를 각각 `ok=False`로 정확히 구분하는지 검증한다. 실제 Slack 채널로의 전달은 이
테스트 범위 밖이며, self-test(위 섹션)가 그 역할을 대신한다.

## 에스컬레이션

`notify_selftest_*` finding이 원인 조치 후에도 24시간 이상 해소되지 않으면(웹훅
자체가 살아있는데도 실패하거나, self-test 스케줄러가 죽어 있다면) 플랫폼 엔지니어링
리드에게 에스컬레이션한다 -- 이 경로가 죽어 있으면 critical 알림(A4/A5/A7/A8/A11)도
조용히 묻힌다.
