# Temporal (temporalio/temporal + temporalio/sdk-python) 코드 레벨 분석 — AIOS Durable Workflow & Ownership Plane 설계 검토

- 대상 저장소: `github.com/temporalio/temporal` (Go 서버), `github.com/temporalio/sdk-python` (Python SDK)
- 클론 방식: `git clone --depth 1` (shallow), 로컬 경로 `scratchpad/ext2/{temporal,sdk-python}`
- 클론 시점 HEAD: temporal `cd667daadb88` (2026-09-02), sdk-python `d0e075c1e25b` (2026-09-01) — 둘 다 조사일 기준 최근 1~2일 이내 커밋. 매우 활발한 프로젝트.
- 라이선스: 두 저장소 모두 **MIT** (`LICENSE`, "Copyright (c) 2025 Temporal Technologies Inc. ... Copyright (c) 2020 Uber Technologies, Inc.") — 상용 사용에 제약 없음, 코드 포크/재배포 자유.
- 이 문서는 AIOS의 미결 질문("Durable Workflow & Ownership Plane을 Temporal로 갈 것인가, 자체 lease 테이블로 충분한가")에 답하기 위한 근거 자료다.

---

## 1. 핵심 실행 모델: Workflow와 Activity

### 1.1 개념 정의

Temporal의 프로그래밍 모델은 두 계층으로 나뉜다.

- **Workflow**: 오케스트레이션 로직. 결정적(deterministic)이어야 하며, 직접 I/O를 하지 않는다. 대신 Activity를 호출해 side effect를 위임한다. Workflow 코드는 이벤트 히스토리로부터 재생(replay)될 수 있어야 하므로 "무엇을 할지 결정하는 코드"이지 "실제로 하는 코드"가 아니다.
- **Activity**: 실제 I/O가 일어나는 곳(DB 쓰기, HTTP 호출, 외부 API, 파일 시스템 등). 비결정적이어도 되고, 실패 시 재시도된다.

### 1.2 Python SDK 데코레이터 API

`@workflow.defn` — 워크플로 클래스 등록 (`sdk-python/temporalio/workflow/_definition.py:56-101`):

```python
def defn(
    cls: ClassType | None = None,
    *,
    name: str | None = None,
    sandboxed: bool = True,
    dynamic: bool = False,
    failure_exception_types: Sequence[type[BaseException]] = [],
    versioning_behavior: temporalio.common.VersioningBehavior = ...,
) -> Callable[[ClassType], ClassType]:
    """Decorator for workflow classes. ... sandboxed: Whether the workflow
    should run in a sandbox. Default is true."""
```

`@workflow.run` — 워크플로의 진입점 메서드, 클래스당 정확히 하나여야 하고 반드시 `async def`여야 함 (`_definition.py:124-148`):

```python
def run(fn: CallableAsyncType) -> CallableAsyncType:
    if not inspect.iscoroutinefunction(fn):
        raise ValueError("Workflow run method must be an async function")
    if "<locals>" in fn.__qualname__:
        raise ValueError("Local classes unsupported, @workflow.run cannot be on a local class")
    setattr(fn, "__temporal_workflow_run", True)
    return fn
```

`@activity.defn` — Activity 함수 등록, sync/async 모두 가능 (`sdk-python/temporalio/activity.py:58-91`):

```python
def defn(
    fn: CallableType | None = None,
    *, name: str | None = None,
    no_thread_cancel_exception: bool = False,
    dynamic: bool = False,
):
    """Decorator for activity functions. Activities can be async or non-async."""
```

### 1.3 결정성 강제 메커니즘 — Sandbox

Python SDK는 워크플로 코드를 별도 sandbox 임포터/실행기(`temporalio/worker/workflow_sandbox/`)에서 돌린다. `_restrictions.py`의 `SandboxRestrictions.invalid_module_members_default`는 표준 라이브러리 중 비결정적인 함수 호출을 런타임에 차단한다 (`_restrictions.py:551-649`):

```python
"datetime": SandboxMatcher(
    children={
        "date": SandboxMatcher(use={"today"}),
        "datetime": SandboxMatcher(use={"now", "today", "utcnow"}),
    }
),
...
"os": SandboxMatcher(
    children={"path": SandboxMatcher.none if sys.version_info >= (3, 14) else SandboxMatcher.all},
    access={"name"}, use={"*"},
),
...
"asyncio": SandboxMatcher(children={
    "as_completed": SandboxMatcher(children={"__call__": SandboxMatcher(
        leaf_warning=UserWarning,
        leaf_message="asyncio.as_completed() is non-deterministic, use workflow.as_completed() instead",
    )}),
})
```

즉 `datetime.now()`, `os.*`(경로 조작 제외 대부분), `http`/`ftplib`/`imaplib`(네트워크), `multiprocessing`, `mmap` 등을 워크플로 코드 안에서 쓰면 `RestrictedWorkflowAccessError`(`NondeterminismError`의 서브클래스, `_restrictions.py:51`)가 발생한다. 대신 SDK가 결정적 대체 API를 제공한다 (`temporalio/workflow/_context.py:749-869`):

```python
def now() -> datetime:
    """Current time from the workflow perspective."""
    return datetime.fromtimestamp(time(), timezone.utc)

def random() -> Random:
    """Get a deterministic pseudo-random number generator."""
    return _Runtime.current().workflow_random()

def uuid4() -> uuid.UUID:
    """Get a new, determinism-safe v4 UUID based on :py:func:`random`."""
```

이 함수들은 워크플로 히스토리에 기록된 시드/타임스탬프를 replay 시 그대로 재사용하므로, 동일 히스토리에 대해 항상 동일한 값을 반환한다.

### 1.4 Replay 개념

Temporal Server는 워크플로의 각 실행을 소스가 아니라 **이벤트 히스토리**로 영속화한다. 워커는 워크플로 상태를 메모리에 들고 있다가, 프로세스가 재시작되거나 캐시에서 축출되면 히스토리 이벤트를 처음부터 다시 실행(replay)해서 동일한 메모리 상태를 재구성한다. 이것이 성립하려면 워크플로 코드가 히스토리에 기록된 것과 무관한 어떤 값도 만들어내면 안 된다 — 그래서 1.3의 sandbox 제약이 필요하다. `sdk-python/temporalio/worker/_replayer.py`의 `Replayer` 클래스가 이 replay를 오프라인으로(서버 연결 없이 히스토리 파일만으로) 실행할 수 있는 도구를 제공한다 — 이는 배포 전 "이 워크플로 코드 변경이 기존 in-flight 실행을 깨뜨리지 않는가"를 검증하는 CI 게이트로 쓰인다.

### AIOS 시사점

- **패턴만 채택**: "오케스트레이션 코드(무엇을 할지)"와 "I/O 코드(실제로 하는 것)"를 물리적으로 분리하는 Workflow/Activity 이분법 자체는 AIOS의 전략 검증 파이프라인·배포 승인·DevEngine PR 리뷰 같은 다단계 워크플로 설계에 그대로 차용할 가치가 있다 — Temporal 인프라 없이도 "이 함수는 결정적이어야 한다"는 규율을 코드 리뷰 체크리스트/린터 룰로만 넣어도 절반은 얻는다.
- **전면 도입은 과함**: sandbox의 정교함(수백 개 stdlib 멤버를 개별적으로 화이트/블랙리스트 처리)은 프레임워크가 대신 관리해줄 때만 가치가 있다. AIOS가 직접 이 수준의 sandbox를 구현하는 것은 비합리적이며, 이는 오직 "임의의 미래 코드가 결정적으로 재생 가능해야 한다"는 요구가 있을 때만 정당화된다.
- Replay 기반 결정성 검증은 AIOS의 Strategy Registry가 이미 갖고 있는 `artifact_hash` 불변성 요구와 철학적으로 유사하다 — "과거에 실행된 것과 현재 코드가 다르면 명시적으로 감지하라"는 점에서 배울 점이 있다(§5 참조).

---

## 2. 내구성 메커니즘: 이벤트 소싱과 샤드 모델

### 2.1 이벤트 히스토리 저장 형태

Temporal Server는 워크플로 실행 상태를 "현재 값"이 아니라 "여기까지 일어난 이벤트들의 append-only 로그"로 저장한다(Event Sourcing). 이력은 브랜치 구조로 저장되어 워크플로 리셋(reset)이나 재시도 시 특정 지점에서 새 브랜치를 딸 수 있다. `common/persistence/persistence_interface.go:536-551`의 저수준 append 요청 구조:

```go
InternalAppendHistoryNodesRequest struct {
    BranchToken []byte              // The raw branch token
    IsNewBranch bool                // True if it is the first append request to the branch
    Info        string              // The info for clean up data in background
    BranchInfo  *persistencespb.HistoryBranch
    TreeInfo    *commonpb.DataBlob  // Serialized TreeInfo
    Node        InternalHistoryNode // The history node
    ShardID     int32
}
```

실제 쓰기는 `common/persistence/history_manager.go:482`의 `AppendHistoryNodes` 메서드가 수행하며, `executionManagerImpl`이 SQL(Postgres/MySQL) 또는 Cassandra 드라이버로 직렬화한다.

### 2.2 샤드 모델과 소유권 검증 — AIOS P0-R1과 가장 직접적으로 비교되는 지점

Temporal history 서비스는 워크플로 실행을 고정된 개수의 **샤드**로 해시 분산하고, 클러스터 멤버십(gossip 기반 ring)을 통해 각 샤드를 정확히 한 호스트가 소유하도록 보장한다. `service/history/shard/ownership.go:132-147`:

```go
// verifyOwnership checks if the shard should be owned by this host's shard
// controller. If membership lists another host as the owner, it returns a
// ShardOwnershipLost error with the correct owner.
func (o *ownership) verifyOwnership(shardID int32) error {
    ownerInfo, err := o.historyServiceResolver.Lookup(convert.Int32ToString(shardID))
    if err != nil {
        return err
    }
    hostInfo := o.hostInfoProvider.HostInfo()
    if ownerInfo.Identity() != hostInfo.Identity() {
        return serviceerrors.NewShardOwnershipLost(ownerInfo.Identity(), hostInfo.GetAddress())
    }
    return nil
}
```

하지만 진짜 안전성은 멤버십 조회가 아니라 **DB 레벨 조건부 쓰기(fencing token)**에서 나온다. 각 샤드는 `RangeID`(단조 증가 정수)를 가지며, 샤드를 인수(acquire)할 때마다 이 값을 증가시켜 DB에 기록한다. 이후의 모든 쓰기는 이 `RangeID`를 동반해야 하고, DB가 조건부 업데이트로 검증한다. `common/persistence/data_interfaces.go:148-152, 184-219`:

```go
// ShardOwnershipLostError is returned when conditional update fails due to RangeID for the shard
ShardOwnershipLostError struct {
    ShardID int32
    Msg     string
}

// UpdateShardRequest is used to update shard information
UpdateShardRequest struct {
    ShardInfo       *persistencespb.ShardInfo
    PreviousRangeID int64
}

// AddHistoryTasksRequest is used to write new tasks
AddHistoryTasksRequest struct {
    ShardID int32
    RangeID int64   // 이 값이 DB의 현재 RangeID와 다르면 쓰기 거부
    ...
}
```

즉 "구 소유자(죽었거나 네트워크 파티션에 갇힌 프로세스)가 뒤늦게 쓰기를 시도해도, RangeID가 낡았으면 DB가 거부한다." 이는 in-memory 멤버십 체크(§verifyOwnership, race 가능)보다 근본적인 안전장치이며 — **정확히 AIOS가 이미 자체적으로 제안한 "execution_leases 테이블 + fencing token" 설계와 동일한 패턴**이다.

### AIOS 시사점

- **패턴을 그대로 채택할 가치가 큼**: RangeID 방식은 AIOS의 최소 수정안(`execution_leases` 테이블에 단조 증가 fencing token 컬럼 추가, 매 쓰기마다 `WHERE fencing_token = :expected`로 조건부 UPDATE)과 설계적으로 동일하다. Temporal 코드가 검증한 것은 "membership lookup만으로는 불충분하고 반드시 DB 조건부 쓰기가 최종 방어선이어야 한다"는 원칙 — AIOS 자체 구현에도 이 원칙을 명시적으로 반영해야 한다(현재 AIOS 설계에 이미 반영되어 있다면 grep으로 재확인 권장).
- **이벤트 소싱 전체를 도입할 필요는 없음**: AIOS의 5개 백그라운드 루프는 "여기까지 뭘 했는지"를 히스토리 이벤트로 재구성할 필요가 없는, 상태가 단순한(현재 커서/오프셋 정도) 루프다. Temporal 수준의 append-only 브랜치형 이벤트 로그는 오버엔지니어링이다.
- 다단계 워크플로(전략 검증, 배포 승인, DevEngine PR 리뷰)는 상태가 좀 더 복잡하고 사람 승인 대기 등 장시간 대기가 끼어들 수 있어, 이 부분만큼은 이벤트 소싱형 상태 기계(간소화된 버전)를 자체 구현하거나 Temporal Workflow로 실제로 얹는 것이 정당화될 여지가 있다(§7 참조).

---

## 3. Task Queue와 워커 폴링 모델

### 3.1 워커가 작업을 가져가는 방식

Temporal은 브로커가 작업을 push하는 방식이 아니라, 워커가 Task Queue를 **롱폴링(long-poll)** 하는 pull 모델이다. Python SDK의 `Worker` 생성자(`sdk-python/temporalio/worker/_worker.py:105-150`)는 폴러 동작을 세밀하게 제어하는 파라미터를 노출한다:

```python
task_queue: str,
...
max_concurrent_workflow_task_polls: int | None = None,
nonsticky_to_sticky_poll_ratio: float = 0.2,
max_concurrent_activity_task_polls: int | None = None,
...
workflow_task_poller_behavior: PollerBehavior = PollerBehaviorSimpleMaximum(...),
activity_task_poller_behavior: PollerBehavior = PollerBehaviorSimpleMaximum(...),
```

`PollerBehaviorAutoscaling`(`_worker.py:69-78`)는 서버 피드백을 보고 폴러 수를 자동 확장/축소한다 — 워커가 여러 대 떠 있어도 동일 Task Queue를 두고 서버가 "슬롯 있는 워커에게" 작업을 나눠주는 구조다. 실제 폴링 루프 자체는 Python 코드가 아니라 Rust로 작성된 Core SDK(`temporalio/bridge`)에 위임되어 있으며, Python SDK는 이를 감싸는 얇은 레이어다.

### 3.2 Sticky Queue

`nonsticky_to_sticky_poll_ratio`가 보여주듯, 한 번 워크플로를 처리한 워커는 이후 같은 워크플로의 작업을 우선적으로 다시 받도록(sticky) 최적화되어 있다(메모리 캐시 재사용, replay 비용 절감). 워커가 죽거나 sticky 큐가 타임아웃되면 다른 워커가 non-sticky 큐에서 그 작업을 가져가고, 그 워커는 히스토리 전체를 replay해서 상태를 복구한다.

### AIOS 시사점

- **패턴만 채택**: pull 기반 폴링과 "여러 워커가 동일 큐를 경쟁적으로 당겨가되 서버가 슬롯을 배분한다"는 아이디어는 AIOS의 execution ownership 문제(여러 인스턴스가 동일 루프를 중복 실행할 위험)에 직접 적용 가능한 개념이다. 다만 AIOS는 Task Queue라는 별도 서비스를 새로 두지 않고, 기존 Postgres의 `SELECT ... FOR UPDATE SKIP LOCKED`류 패턴으로 "당겨가기 경쟁"을 구현하는 쪽이 훨씬 저렴하다.
- Sticky 큐 최적화는 "replay 비용을 줄이기 위한" 것으로, AIOS가 이벤트 소싱 replay 모델을 채택하지 않는 한 적용할 대상 자체가 없다 — 도입 불필요.

---

## 4. 실패/재시도 시맨틱: 활동 재시도, 타임아웃, 죽은 워커 감지

### 4.1 RetryPolicy 형태

`sdk-python/temporalio/common.py:37-107`의 `RetryPolicy` dataclass:

```python
@dataclass
class RetryPolicy:
    initial_interval: timedelta = timedelta(seconds=1)
    backoff_coefficient: float = 2.0
    maximum_interval: timedelta | None = None
    maximum_attempts: int = 0          # 0 = 무제한
    non_retryable_error_types: Sequence[str] | None = None
```

검증 로직(`common.py:93-107`)은 `maximum_attempts == 1`이면 경고, `backoff_coefficient < 1`이면 에러 등 상식적인 가드레일을 포함한다. 이 정책은 Activity 호출마다 지정할 수 있고, 재시도 자체는 워커가 아니라 **서버가** 스케줄링한다(워커가 죽어도 재시도 타이머는 서버 쪽 영속 상태에 있으므로 살아남는다).

### 4.2 타임아웃 기반 죽은 워커 감지 — 서버 측 근거

Temporal은 "워커가 살아있는지"를 heartbeat 프로토콜이 아니라 **타이머 태스크**로 감지한다. `service/history/timer_queue_active_task_executor.go`에 세 종류의 타임아웃 실행기가 있다:

- `executeWorkflowTaskTimeoutTask` (`timer_queue_active_task_executor.go:382`) — 워커가 워크플로 태스크를 받고도 `StartToCloseTimeout` 안에 완료 응답을 못 주면 태스크를 실패 처리하고 재스케줄한다.
- `executeActivityTimeoutTask` (`timer_queue_active_task_executor.go:204`) — Activity의 `ScheduleToStart`/`StartToClose`/`Heartbeat`/`ScheduleToClose` 타임아웃을 각각 감지. 특히 heartbeat 타임아웃은 아래처럼 별도 처리된다:

```go
// Need to clear activity heartbeat timer task mask for new activity timer task creation.
// NOTE: LastHeartbeatTimeoutVisibilityInSeconds is for deduping heartbeat timer creation...
isHeartBeatTask := task.TimeoutType == enumspb.TIMEOUT_TYPE_HEARTBEAT
ai, heartbeatTimeoutVis, ok := mutableState.GetActivityInfoWithTimerHeartbeat(task.EventID)
if isHeartBeatTask && ok && queues.IsTimeExpired(task, task.GetVisibilityTime(), ...) {
    ...
}
```

- `executeActivityRetryTimerTask` (`timer_queue_active_task_executor.go:540`) — RetryPolicy에 따른 다음 재시도 시각이 되면 Activity를 다시 스케줄.

핵심은: 이 감지는 **워커 프로세스가 아니라 워크플로 실행이 속한 샤드를 소유한 history 서비스 인스턴스가, 자신의 영속 타이머 큐를 폴링해서** 수행한다. 워커가 크래시해도 서버는 (a) 자체 타이머로 무응답을 알아채고, (b) 같은 Task Queue에 다시 태스크를 올려서 다른 워커가 채가게 한다 — "리스 만료 → 재획득"과 동일한 구조다.

### AIOS 시사점

- **패턴을 그대로 채택**: "타임아웃 감지와 작업 재배정을 죽은 프로세스 자신이 아니라 별도의 감시자(watchdog)가 수행한다"는 원칙은 AIOS의 lease 테이블 설계에도 필요하다 — lease에 `expires_at`을 두고, 별도 스케줄러(또는 단순 폴링 쿼리)가 만료된 lease를 회수하는 방식이면 충분히 동일 효과를 얻는다. Temporal의 3종 타임아웃(schedule-to-start/start-to-close/heartbeat) 구분은 AIOS lease 설계에도 "루프가 애초에 못 떴다" vs "뜨긴 했는데 응답이 없다"를 구분하는 데 참고할 가치가 있다.
- **RetryPolicy의 정교함은 이번 스코프에서 불필요**: AIOS의 5개 루프는 현재 "재시도 없이 다음 tick에 다시 시도" 수준이면 충분해 보인다. exponential backoff + non-retryable 예외 화이트리스트 같은 세밀함은 실제로 반복 실패가 문제였던 루프가 나올 때 그때 추가해도 늦지 않다.
- 서버가 재시도를 영속 상태로 스케줄링(워커 생사와 무관)한다는 점은, AIOS가 lease/큐를 자체 Postgres 테이블로 구현할 때도 "재시도 타이머는 애플리케이션 프로세스 메모리가 아니라 DB row에 있어야 한다"는 원칙으로 일반화할 수 있다 — 이미 자체안이 DB 테이블 기반이므로 자연히 만족됨.

---

## 5. 버저닝 / 결정성-안전 코드 변경 — Patching API

### 5.1 문제

이벤트 소싱 replay 모델의 근본적 위험은: 워크플로가 실행 중(in-flight)인 상태에서 코드를 배포하면, 새 코드로 replay했을 때 과거 히스토리와 다른 분기를 타서 **비결정성 에러**가 나거나 조용히 잘못된 상태가 될 수 있다는 것이다.

### 5.2 `workflow.patched()` API

`sdk-python/temporalio/workflow/_context.py:762-780`:

```python
def patched(id: str) -> bool:
    """Patch a workflow.

    When called, this will only return true if code should take the newer path
    which means this is either not replaying or is replaying and has seen this
    patch before.

    Use :py:func:`deprecate_patch` when all workflows are done and will never be
    queried again. The old code path can be used at that time too.

    Args:
        id: The identifier for this patch. This identifier may be used
            repeatedly in the same workflow to represent the same patch
    """
    return _Runtime.current().workflow_patch(id, deprecated=False)
```

전형적인 사용 패턴(문서상 관용구):

```python
if workflow.patched("new-approval-step"):
    await workflow.execute_activity(new_approval_activity, ...)
else:
    await workflow.execute_activity(old_approval_activity, ...)
```

이 호출 자체가 이벤트 히스토리에 마커(marker event)로 기록되므로, replay 시 "이 워크플로 실행이 이 패치를 이미 봤는가"를 히스토리에서 조회해서 분기를 고정한다 — 오래된 실행은 계속 구 코드 경로로, 새 실행(또는 이 지점을 아직 지나지 않은 실행)은 새 코드 경로로 replay된다. 모든 워크플로가 이 지점을 지나 완료되면 `deprecate_patch(id)`로 마커 자체를 없애 코드를 정리한다(`_context.py:553`).

### 5.3 `versioning_behavior` (Worker Deployment 버저닝)

`workflow.defn`의 `versioning_behavior` 파라미터(`_definition.py:43, 63, 85`)는 더 최신의 접근으로, 워크플로 전체를 특정 "Worker Deployment 버전"에 고정하거나(PINNED) 최신 버전으로 자동 이동(AUTO_UPGRADE)시키는 서버 레벨 기능이다. `patched()`가 코드 내부의 세밀한 분기 제어라면, 이쪽은 배포 단위의 버전 고정이다.

### AIOS 시사점

- **패턴을 강하게 채택**: AIOS의 Strategy Registry가 이미 `artifact_hash`로 불변성을 강제하고 있다면, "실행 중인 워크플로/전략이 참조하는 코드 버전과 현재 배포된 코드 버전이 다를 때 어떻게 할 것인가"라는 질문에 Temporal의 `patched()` idiom — **명시적 마커 + 히스토리 조회로 분기 고정** — 은 AIOS가 자체 승인 파이프라인(배포 승인, DevEngine PR 리뷰)에서 "이 승인 프로세스가 시작된 시점의 룰 버전을 계속 써야 한다"는 요구에 직접 응용 가능하다. 즉 자체 상태 테이블에 `rule_version_seen` 같은 컬럼을 두고 진행 중인 프로세스는 시작 시점 버전으로 고정, 신규 프로세스만 새 버전을 타게 하면 동일 효과.
- **`patched()` 그 자체(마커 이벤트 자동 기록·replay 시 자동 조회)는 Temporal 엔진의 기능**이므로, 이걸 그대로 재현하려면 이벤트 소싱을 도입해야 한다 — AIOS가 다단계 워크플로(전략 검증, 배포 승인, DevEngine PR 리뷰)를 Temporal 위에서 실제로 돌린다면 이 기능은 "공짜로" 따라오지만, 자체 구현한다면 그 워크플로 엔진 부분만 별도로 설계해야 하는 비용이 있다.

---

## 6. 운영 부담: 셀프호스팅 요구사항과 규모 대비 과잉 여부

### 6.1 필요한 백엔드

`develop/docker-compose/docker-compose.yml`(temporal 저장소, 개발/통합테스트용 구성)은 다음을 띄운다: MySQL, Cassandra, PostgreSQL, Elasticsearch, Prometheus, Grafana, Tempo, temporal-ui. 이건 "모든 백엔드 조합을 테스트하기 위한" 개발자용 compose이지 프로덕션 권장 구성이 아니다.

실제 최소 요구사항은 `config/development-postgres12.yaml`(`config/development-postgres12.yaml:1-32`)에서 확인된다 — **PostgreSQL 하나만으로 defaultStore와 visibilityStore를 모두 구성**할 수 있다:

```yaml
persistence:
  defaultStore: postgres-default
  visibilityStore: postgres-visibility
  numHistoryShards: 4
  datastores:
    postgres-default:
      sql: {pluginName: "postgres12", databaseName: "temporal", ...}
    postgres-visibility:
      sql: {pluginName: "postgres12", databaseName: "temporal_visibility", ...}
```

Elasticsearch는 **고급 visibility(복잡한 검색 쿼리)에만 필요**하고 필수가 아니다. 또한 `config/development-sqlite.yaml`, `config/development-sqlite-file.yaml`이 존재하는 것에서 보듯 SQLite 단일 파일로도 개발/저부하 환경을 돌릴 수 있다(공식 CLI `temporal server start-dev`가 바로 이 모드를 씀 — 이 부분은 별도 저장소인 `temporalio/cli`의 기능이라 코드 확인은 못 했고 docs.temporal.io 기준 개념 인용).

### 6.2 프로세스 구성

`config/development-postgres12.yaml`의 `services:` 블록이 보여주듯, Temporal Server는 논리적으로 4개 서비스(frontend, matching, history, worker)로 구성되며 각각 별도 gRPC/멤버십 포트를 가진다. 단일 바이너리 안에서 4개를 모두 구동하는 것(`temporal-server start`의 all-in-one 모드)도 가능하지만, 이는 여전히 AIOS의 FastAPI 프로세스와는 별개의 장수(long-running) 프로세스+포트 세트이며, 별도 배포 파이프라인·헬스체크·모니터링 대상이 하나 늘어남을 의미한다.

### 6.3 규모 대비 판단

AIOS 현재 규모(단일 Postgres, 낮은 주문량, 5개 백그라운드 루프)를 기준으로 보면:

- Postgres 하나만 추가로 붙이는 최소 구성이 가능하다는 점은 "Temporal = Cassandra+ES 필수"라는 흔한 오해보다는 진입 장벽이 낮다.
- 그럼에도 (a) 별도 서버 프로세스(들)을 새로 배포·운영해야 하고, (b) Python 워커 프로세스가 Rust Core(`temporalio.bridge`, 네이티브 확장)에 의존하게 되어 배포 아티팩트가 복잡해지고, (c) 팀이 이벤트 히스토리 크기 관리, 워크플로 히스토리 길이 제한(continue-as-new 패턴 학습) 같은 Temporal 고유의 운영 지식을 새로 습득해야 한다.
- AIOS가 풀려는 문제(execution ownership lease)는 이 모든 것을 요구하지 않는다 — Postgres 테이블 하나, fencing token 컬럼, 조건부 UPDATE 정도로 끝난다.

### AIOS 시사점

- **당장은 과함**: 현재 스코프(리스/오너십)만 놓고 보면 Temporal 전체 스택 도입은 명백히 과잉 엔지니어링이다. 문제 크기 대비 인프라 풋프린트가 맞지 않는다.
- **다단계 워크플로 스코프는 조건부로 재고 가능**: 만약 "전략 검증 파이프라인 + 배포 승인 + DevEngine PR 리뷰"가 실제로 수 시간~수 일에 걸친 사람 대기(human-in-the-loop)를 포함하고, 여러 서비스에 걸친 사가(saga)형 보상 로직이 필요해진다면, 이 부분만 별도로 Temporal을 붙이는 것은(단, Postgres 백엔드로 최소 구성) 재검토할 가치가 있다 — 단, 이는 "지금" 결정할 문제가 아니라 그 요구가 실제로 발생했을 때의 결정이다.
- MIT 라이선스이고 최근 커밋이 매우 활발(1~2일 이내)하므로, 라이선스나 유지보수 중단 리스크는 낮다 — 이 결정을 가로막는 것은 라이선스가 아니라 순수하게 "문제 크기 대비 인프라 크기"의 비례성 문제다.

---

## 7. AIOS가 지금 필요한 것 vs Temporal이 추가로 주는 것

### 7.1 AIOS의 최소안 (자체 구현)

AIOS 자체 감사에서 제안된 최소 수정안: `execution_leases` 테이블 + 단조 증가 fencing token 컬럼. 각 백그라운드 루프가 시작 시 `INSERT ... ON CONFLICT DO UPDATE ... WHERE expires_at < now() RETURNING fencing_token`류의 쿼리로 리스를 획득하고, 이후 모든 부수효과 쓰기에 그 토큰을 동반한다. 새 인프라 없음(기존 Postgres 재사용), 새 배포 프로세스 없음, 학습 곡선 낮음.

### 7.2 Temporal이 "추가로" 주는 것 (자체안에는 없는 것)

| 기능 | 자체 lease 테이블 | Temporal |
|---|---|---|
| 중복 실행 방지(리스/펜싱) | O (직접 구현) | O (동일 원리, 이미 검증된 구현) |
| 임의 길이의 다단계 워크플로 상태 영속 | X (직접 상태 기계를 짜야 함) | O (이벤트 히스토리 + replay) |
| 사람 승인 대기(수 시간~수 일) 중 프로세스 재시작 안전성 | X (별도 설계 필요) | O (Signal/Update로 자연스럽게 표현) |
| 크로스 서비스 사가(보상 트랜잭션) 오케스트레이션 | X (직접 구현) | O (Activity 재시도 + 명시적 보상 워크플로) |
| 코드 버전 변경 시 in-flight 안전성(`patched()`) | X (직접 마커 설계 필요) | O |
| 가시성(어떤 워크플로가 어느 단계에 멈춰 있는지 UI로 조회) | X | O (Web UI, `tdbg` 등) |
| 운영 풋프린트 | 거의 0 | Postgres 추가 스토어 + 서버 프로세스(들) + 워커 프로세스 |

### 7.3 판단 기준

- 지금 당장 막혀 있는 문제(중복 실행 위험)는 표의 첫 줄만 필요하다 — Temporal의 나머지 기능은 이 문제에 대해 아무것도 더 사주지 않는다.
- "전략 검증 파이프라인/배포 승인/DevEngine PR 리뷰"가 정말로 "장시간 실행 + 사람 대기 + 크로스 서비스 사가"의 성격을 강하게 띤다면, 표의 2~5번째 줄이 의미를 가지기 시작한다. 하지만 이 세 파이프라인이 실제로 그 정도로 복잡한지(며칠씩 걸리는지, 여러 마이크로서비스에 걸친 보상 로직이 필요한지)는 이 조사의 범위 밖이며 AIOS 팀이 자체적으로 판단해야 한다.
- "Durable Workflow & Ownership Plane"이라는 하나의 이름 아래 두 가지 다른 문제(오너십/리스, 장기 워크플로)를 묶은 것 자체가 설계상 위험 신호다 — 둘은 요구사항 곡선이 다르고, 하나를 위해 다른 하나까지 딸려서 무거운 해법을 택할 이유가 없다.

---

## 최종 판단: 지금 당장 도입할 가치가 있는가, 아니면 패턴만 배우고 자체 구현으로 충분한가

**결론: 지금은 자체 구현으로 충분하다. Temporal 전면 도입은 시기상조(disproportionate)다.**

근거:

1. AIOS가 실제로 막혀 있는 문제(P0-R1: 5개 루프의 DB 레벨 리스 부재로 인한 중복 실행 위험)는 Temporal의 샤드 오너십 메커니즘(§2.2 RangeID/fencing token)과 개념적으로 동일하지만, Temporal 코드가 보여주는 해법 자체는 "DB 조건부 쓰기 + 단조 토큰"이라는 몇 줄짜리 원칙이다. 이 원칙을 얻기 위해 별도 서버 프로세스, 별도 스토어, Rust 네이티브 확장 의존성, 새로운 운영 지식 전체를 들여올 필요가 없다.
2. Temporal이 진짜로 승부하는 영역 — 이벤트 소싱 기반 replay, `patched()`를 통한 in-flight 코드 버전 안전성, 사람 승인 대기를 포함한 장기 워크플로, 크로스 서비스 사가 — 은 AIOS의 세 파이프라인(전략 검증/배포 승인/DevEngine PR 리뷰)에 잠재적으로 유용하지만, 이 조사만으로는 그 파이프라인들이 실제로 "며칠씩 걸리는 사람 대기"나 "여러 서비스에 걸친 보상 트랜잭션"을 필요로 하는지 확인되지 않았다. 필요가 확인되지 않은 상태에서 인프라를 먼저 들이는 것은 순서가 거꾸로다.
3. Temporal은 MIT 라이선스이고 활발히 유지보수되므로, "나중에 실제로 그 정도 복잡도가 확인되면" 그때 도입해도 늦지 않다 — 지금 도입해서 얻는 옵션 가치보다 지금 짊어지는 운영 비용(별도 프로세스, Postgres 추가 스토어, 네이티브 확장 의존성, 팀의 학습 곡선)이 크다.

**권고**:

- P0-R1(중복 실행 방지)은 AIOS 자체안(`execution_leases` + fencing token, Temporal §2.2의 RangeID 패턴을 참고해 설계 검증)으로 즉시 진행.
- "Durable Workflow & Ownership Plane"이라는 이름에서 "Ownership"(리스)과 "Workflow"(다단계 승인/파이프라인)를 설계 문서 레벨에서 분리하고, Workflow 쪽은 세 파이프라인 각각이 실제로 얼마나 오래 걸리고 얼마나 복잡한 상태 전이를 갖는지 먼저 계측한 뒤 — 그 결과가 "단순 상태 머신 + Postgres 테이블"로 충분한지 "Temporal이 실제로 필요한지"를 판단하는 별도 후속 검토로 미룬다.
- 만약 향후 Temporal을 도입하게 되면, Postgres 단일 백엔드 구성(§6.1, `development-postgres12.yaml` 패턴)으로 시작해 Cassandra/Elasticsearch 없이 최소 풋프린트로 시작하는 것을 권장.
