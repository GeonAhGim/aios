# RB-09: 백업 복구(PITR) 절차

Spec: `docs/design/ADR-2026-09-09-B-mvp1-hardening-and-mvp2-scope.md` H-4,
FA-20(`docs/specs/L4_ibor_fund_accounting_and_resilience_v1.0.md`),
`docs/blue_team/09-current-aios-implementation-review-and-development-policy.md` §10,
`scripts/backup/{base_backup,wal_archive,restore_drill}.py`, `ops/backup_verify.py`.

## 트리거 알림

- `backup_drill_missing` / `backup_drill_unreadable` / `backup_drill_failed` /
  `backup_drill_stale`(전부 high, `C:\aios\pm\healthcheck.py`의 `check_backup_drill()`) —
  24시간 안에 성공한 복구 리허설이 없다는 뜻이다. 이 알림 자체는 "실제 장애"가
  아니라 "장애가 나도 복구할 수 있다는 증거가 없다"는 신호이므로, 원인 파악과
  별개로 §"복구 리허설이 실패했을 때" 절차를 먼저 밟는다.
- 실제 DB 장애(RB-01 readiness 실패, 데이터 파손·오삭제 등)로 PITR이 필요해진
  경우 아래 §"실제 복구 절차"로 바로 간다.

## 배경

블루팀 감사(위 §10)가 지적한 것: "backup 있음"만으로는 부족하다. 필요한 사슬은

```
backup -> restore -> consistency verification -> healthcheck -> periodic drill
```

이고, "백업 파일을 만든다"가 아니라 "실제로 복구할 수 있음을 반복 증명한다"가
목표다. 세 스크립트가 파일당 한 책임으로 이 사슬을 나눠 가진다:

- `scripts/backup/base_backup.py` — `pg_basebackup` 래퍼. 물리 베이스 백업 1회 생성 +
  `manifest.json`(성공/실패, 시각, stderr tail) 기록. 실패작은 자동으로 지운다.
- `scripts/backup/wal_archive.py` — PITR 전제조건(`wal_level`/`archive_mode`/
  `archive_command`)이 실제로 켜져 있는지, `--verify-write`로는 WAL 스위치 후
  아카이브 디렉터리에 새 파일이 실제로 쌓이는지까지 검증한다.
- `scripts/backup/restore_drill.py` — 가장 최근 성공 베이스 백업 + 아카이브된 WAL을
  **별도 데이터 디렉터리·별도 포트**로 복구하고, `scripts/replay_verify.py`를 그
  인스턴스에 대해 그대로 돌려 "복구됨"이 아니라 "복구된 데이터가 운영 테이블과
  byte-identical함"을 증명한다. 성공/실패 무관하게 임시 인스턴스는 항상 정리하고,
  결과를 `runtime/backup/drill_latest.json`(로컬)과 `C:\aios\pm\backup\drill_latest.json`
  (fleet healthcheck용)에 남긴다.

## 정기 리허설(일 1회)

`ops/backup_verify.py`(FA-20/task-2677)가 아래 세 단계를 한 커맨드로 순서대로
묶는다 — 앞 단계가 실패하면 뒷 단계는 건너뛰고 그 지점까지만 결과를 남긴다:

```
python -m ops.backup_verify --archive-dir <아카이브 디렉터리> \
  --dest-dir <베이스 백업 상위 디렉터리> \
  --restore-data-dir <운영과 분리된 복구용 데이터 디렉터리> \
  --restore-port <운영과 겹치지 않는 포트> \
  --verify-wal-write
```

세 단계는 각각:

1. `base_backup` — 운영 DB에서 물리 베이스 백업 1회(`scripts/backup/base_backup.py`).
2. `wal_archive` — PITR 전제조건 설정 검증, `--verify-wal-write`를 주면 WAL
   스위치 후 실제 아카이빙까지 검증(`scripts/backup/wal_archive.py`). 위반이면
   즉시 멈추고 §"WAL 아카이빙이 죽어 있을 때"로 간다 — 복구 리허설은 시도조차
   하지 않는다(다음 WAL부터 끊겨 있으면 PITR이 그 시점 이후로 불가능하므로
   의미가 없다).
3. `restore_drill` — 1)의 최신 성공 백업을 별도 인스턴스로 복구하고
   replay_verify까지 실행한다(`scripts/backup/restore_drill.py`). 실패해도
   항상 임시 인스턴스를 내리고 임시 데이터 디렉터리를 지운다.

`--schedule-interval-hours 24`를 주면 OS cron 없이도 그 주기로 무한 반복한다
(`check_backup_drill`의 24시간 기준과 기본값이 맞춰져 있다). cron/Task
Scheduler가 있는 배포 대상은 그쪽에서 하루 1회 인자 없이(1회 실행) 호출하는
것이 기본이다. 어느 경로든 exit code 0(또는 스케줄 모드의 매 주기 report)이어야
그날의 리허설이 유효하다. 세 스크립트를 개별 실행하는 절차(`python -m
scripts.backup.{base_backup,wal_archive,restore_drill}`)도 여전히 동작하며
디버깅 시 한 단계만 격리해 재현할 때 쓴다.

## 복구 리허설이 실패했을 때

1. `C:\aios\pm\backup\drill_latest.json`(또는 로컬 `runtime/backup/drill_latest.json`)의
   `steps`를 본다 — 어느 단계에서 멈췄는지 그대로 남아 있다. `ops/backup_verify.py`로
   돌렸다면 최상위 `steps`는 `base_backup`/`wal_archive`/`restore_drill` 셋뿐이고,
   `restore_drill` 복구 세부 단계는 `steps.restore_drill.detail` 아래 중첩돼 있다:
   - `base_backup`: 베이스 백업 자체가 실패(바이너리 없음/rc≠0) — 뒤 단계는 시도조차
     안 됐다.
   - `wal_archive`: PITR 전제조건 위반 — 뒤 단계(복구 리허설)는 시도조차 안 됐다.
     §"WAL 아카이빙이 죽어 있을 때"로 간다.
   - `restore_drill`(또는 개별 `restore_drill.py` 실행 시 최상위 `steps`) 중첩 단계:
     - `preflight`: `pg_ctl`/`psql`이 PATH에 없다 — 실행 환경 문제, 스크립트가 손댈 일이 아니다.
     - `find_backup`: 성공한 베이스 백업이 하나도 없다 — 정기 리허설 §1을 먼저 확인한다.
     - `restore_files`: 디스크 공간·권한 문제로 백업 파일 복사가 실패했다.
     - `start_postgres`: 복구용 인스턴스가 기동하지 않았다(포트 충돌, 손상된 백업).
     - `wait_recovery`: 기동은 됐지만 `recovery.signal` 처리가 끝나지 않는다 —
       대개 `archive_dir`에 필요한 WAL 파일이 없다(아카이빙이 그 사이 끊겼을 가능성,
       §"WAL 아카이빙이 죽어 있을 때" 확인).
     - `replay_verify`: 복구는 됐지만 데이터가 운영과 다르다 — **가장 심각한 경우**다.
       베이스 백업 자체가 손상됐거나 WAL 재생 로직에 결함이 있다는 뜻이므로, 이
       백업 세대는 복구 신뢰 대상에서 제외하고 플랫폼 엔지니어링 리드에게 즉시
       에스컬레이션한다.
2. 원인을 고치고 정기 리허설 §1~3을 수동으로 다시 돌려 다음 healthcheck 폴링 전에
   `backup_drill_*` finding이 해소되는지 확인한다.

## WAL 아카이빙이 죽어 있을 때

`wal_archive.py`가 위반을 보고하면, 그 시점부터 발생한 WAL은 아카이브되지 않고
있다는 뜻이다 — 이전 베이스 백업으로는 그 시점까지만 PITR이 가능하다.
`archive_command`가 실패하는 동안 Postgres가 오래된 WAL 세그먼트를 `pg_wal/`에
계속 쌓아 디스크를 채울 수 있으므로(archive 성공 전까지는 재사용 안 함), 원인
(대상 디렉터리 권한·용량, 원격 저장소 접속 등)을 우선 해결하고 `archive_command`가
다시 정상 동작하는지 `wal_archive.py --verify-write`로 확인한다.

## 실제 복구 절차 (장애 발생, 라이브 데이터 복구가 필요할 때)

`restore_drill.py`는 **검증용 별도 인스턴스**에만 쓴다 — 실제 운영 복구에 그대로
돌리지 않는다(대상이 운영 DB가 아니라 임시 인스턴스이기 때문). 실제 복구는:

1. 장애 인스턴스를 즉시 내려 추가 쓰기를 막는다(데이터 오삭제 등 사람이 유발한
   장애라면 특히 — 더 쓰기 전에 멈추는 것이 최우선).
2. 목표 복구 시점(PITR target)을 정한다 — 오삭제/오염이 발생한 시각 직전.
3. `base_backup.py`가 남긴 가장 최근 베이스 백업 중 목표 시점 이전인 것을 고른다.
4. `restore_drill.py`의 절차(파일 복사 -> `recovery.signal` + `restore_command` 설정 ->
   기동 -> 복구 완료 대기)를 운영이 쓸 실제 데이터 디렉터리·포트로 수동 수행한다.
   목표 시점까지만 재생하려면 `postgresql.auto.conf`에
   `recovery_target_time = '<목표 시점>'`을 추가한다(생략하면 아카이브된 최신
   WAL까지 전부 재생 -- 리허설의 기본 동작과 같다).
5. `python scripts/replay_verify.py`로 복구된 데이터가 이벤트 재생과 일치하는지
   확인한 뒤에만 트래픽을 되돌린다.
6. 사후 조치: 원인(사람 실수/버그/인프라 장애)을 기록하고, 같은 원인 클래스가
   재발하지 않도록 방지 조치를 별도 리프로 만든다.

## 에스컬레이션

`replay_verify` 단계 실패(베이스 백업 손상 의심), 또는 WAL 아카이빙이 24시간
이상 복구되지 않는 경우 즉시 플랫폼 엔지니어링 리드를 호출한다 — 둘 다 "다음
장애에서 실제로 복구하지 못할 수 있다"는 뜻이라 사람이 확인하기 전까지
`backup_drill_*` finding을 임의로 닫지 않는다.

## 사후 조치

리허설 실패가 설정 드리프트(archive_command 경로 변경, 디스크 정책 변경 등)로
드러나면 원인을 이 문서에 추가하고, 같은 드리프트가 조용히 재발하지 않도록
`wal_archive.py`의 검증 항목에 반영할지 판단한다.
