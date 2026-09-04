import { ApiError, type PaperDeploymentState, type PaperDeploymentView } from "@aios/api-client";
import {
  usePausePaperDeployment,
  useRequestPaperDeployment,
  useResumePaperDeployment,
  useStartPaperDeployment,
  useStopPaperDeployment,
  usePaperDeployments,
} from "@aios/shared-hooks";
import {
  classifyBadRequest,
  classifyForbidden,
  classifyServerError,
  isResourceNotFound,
  routeApiError,
} from "@aios/shared-types";
import {
  Button,
  Card,
  CardTitle,
  EmptyState,
  Field,
  Input,
  LoadingState,
  PageHeader,
  StatusBadge,
} from "@aios/ui-web";
import { useState, type FormEvent } from "react";
import { AppShell } from "../../components/layout/AppShell";
import { BadRequestNotice } from "../../components/BadRequestNotice";
import { ErrorMessage } from "../../components/ErrorMessage";
import { ForbiddenNotice } from "../../components/ForbiddenNotice";
import { NotFoundState } from "../../components/NotFoundState";
import { DuplicateSubmitError, useIdempotentSubmit } from "../../hooks/useIdempotentSubmit";
import { useConflictRetry } from "../../hooks/useConflictRetry";

// spec §9 PLT-15/§3.7: 에러 표시는 ExecutionControlPage(task-901/937)와 동일한
// 세 갈래(400/403/기타) 판정을 재사용한다 — 판정 로직을 다시 만들지 않는다.
function CommandError({ error, onRetry }: { error: unknown; onRetry?: () => void }) {
  if (classifyBadRequest(error)) return <BadRequestNotice error={error} />;
  if (classifyForbidden(error)) return <ForbiddenNotice error={error} />;
  const routed = routeApiError(error);
  const serverError = classifyServerError(error);
  return (
    <ErrorMessage
      errorCode={error instanceof ApiError ? error.errorCode : undefined}
      message={error instanceof Error ? error.message : undefined}
      traceId={error instanceof ApiError ? error.traceId : undefined}
      retryAfterSec={routed.kind === "backoff_retry" ? routed.afterSec : undefined}
      onRetry={onRetry && serverError.kind === "retryable" ? onRetry : undefined}
    />
  );
}

function DeploymentsListError({ error, onRetry }: { error: unknown; onRetry: () => void }) {
  if (isResourceNotFound(error)) {
    return (
      <NotFoundState
        title="배포 목록을 찾을 수 없습니다"
        description="삭제되었거나 존재하지 않는 데이터입니다."
      />
    );
  }
  return <CommandError error={error} onRetry={onRetry} />;
}

// 77번 §2/§3(start_deployment.py/pause_deployment.py 원본 확인): start는 READY→
// RUNNING, resume는 PAUSED→RUNNING, pause는 RUNNING→PAUSED만 허용한다. stop은
// READY/RUNNING/PAUSED/DEGRADED/RECOVERY_REVIEW(_STOPPABLE_STATES)에서 허용되고
// REQUESTED·STOPPED·FAILED에서는 허용되지 않는다 — 버튼 노출을 서버 상태머신과
// 맞춰 눌러도 항상 실패할 조작을 보여주지 않는다.
const STOPPABLE_STATES: ReadonlySet<PaperDeploymentState> = new Set([
  "READY",
  "RUNNING",
  "PAUSED",
  "DEGRADED",
  "RECOVERY_REVIEW",
]);

interface DeploymentRowProps {
  deployment: PaperDeploymentView;
}

function DeploymentRow({ deployment }: DeploymentRowProps) {
  const start = useStartPaperDeployment();
  const resume = useResumePaperDeployment();
  const pause = usePausePaperDeployment();
  const stop = useStopPaperDeployment();
  const { submit: submitStart } = useIdempotentSubmit(`paperDeployments.start:${deployment.id}`);
  const { submit: submitResume } = useIdempotentSubmit(`paperDeployments.resume:${deployment.id}`);
  const { submit: submitPause } = useIdempotentSubmit(`paperDeployments.pause:${deployment.id}`);
  const { submit: submitStop } = useIdempotentSubmit(`paperDeployments.stop:${deployment.id}`);

  async function run(
    submit: typeof submitStart,
    mutateAsync: (vars: { deploymentId: string; idempotencyKey: string }) => Promise<unknown>,
  ) {
    try {
      await submit((idempotencyKey) => mutateAsync({ deploymentId: deployment.id, idempotencyKey }));
    } catch (err) {
      if (err instanceof DuplicateSubmitError) return;
    }
  }

  const activeMutation = [start, resume, pause, stop].find((m) => m.isError);

  return (
    <div className="rounded-lg border border-border bg-surface p-4">
      <div className="flex items-center justify-between">
        <div>
          <p className="font-medium text-fg">{deployment.packageRef}</p>
          <p className="text-sm text-fg-muted">
            연결 {deployment.connectionId ?? "미지정"} · fence {deployment.fenceToken}
          </p>
        </div>
        <StatusBadge status={deployment.state} />
      </div>
      {activeMutation?.isError && <div className="mt-3"><CommandError error={activeMutation.error} /></div>}
      <div className="mt-3 flex gap-2">
        {deployment.state === "READY" && (
          <Button
            type="button"
            variant="secondary"
            size="sm"
            loading={start.isPending}
            onClick={() => void run(submitStart, start.mutateAsync)}
          >
            시작
          </Button>
        )}
        {deployment.state === "PAUSED" && (
          <Button
            type="button"
            variant="secondary"
            size="sm"
            loading={resume.isPending}
            onClick={() => void run(submitResume, resume.mutateAsync)}
          >
            재개
          </Button>
        )}
        {deployment.state === "RUNNING" && (
          <Button
            type="button"
            variant="secondary"
            size="sm"
            loading={pause.isPending}
            onClick={() => void run(submitPause, pause.mutateAsync)}
          >
            일시정지
          </Button>
        )}
        {STOPPABLE_STATES.has(deployment.state) && (
          <Button
            type="button"
            variant="danger"
            size="sm"
            loading={stop.isPending}
            onClick={() => void run(submitStop, stop.mutateAsync)}
          >
            중지
          </Button>
        )}
      </div>
    </div>
  );
}

export function PaperDeploymentsPage() {
  const { data, isLoading, refetch, error: listError, isError: listIsError } = usePaperDeployments();
  const requestDeployment = useRequestPaperDeployment();
  const { submit } = useIdempotentSubmit("paperDeployments.request");
  const [packageRef, setPackageRef] = useState("");
  const [connectionId, setConnectionId] = useState("");
  const [adapterType, setAdapterType] = useState("bitget-sandbox");
  const [providerSandboxAccountRef, setProviderSandboxAccountRef] = useState("");
  const [error, setError] = useState<unknown>(null);

  // §3.3 STATE_CONCURRENCY_CONFLICT(409)는 목록을 refetch한 뒤 1회 재제출한다
  // (ExecutionControlPage task-937과 동일 패턴).
  const { run: requestWithRetry } = useConflictRetry(
    () =>
      submit((idempotencyKey) =>
        requestDeployment.mutateAsync({
          body: {
            packageRef,
            connectionId: connectionId || undefined,
            adapterType,
            providerSandboxAccountRef,
          },
          idempotencyKey,
        }),
      ),
    refetch,
  );

  async function submitRequest() {
    setError(null);
    try {
      await requestWithRetry();
      setPackageRef("");
      setConnectionId("");
      setProviderSandboxAccountRef("");
    } catch (err) {
      if (err instanceof DuplicateSubmitError) return;
      setError(err instanceof ApiError ? err : new Error("배포 요청에 실패했습니다."));
    }
  }

  function handleSubmit(e: FormEvent) {
    e.preventDefault();
    void submitRequest();
  }

  const deployments = data?.deployments ?? [];

  return (
    <AppShell>
      <div className="space-y-8">
        <PageHeader title="페이퍼 배포 제어" />

        <Card>
          <CardTitle>새 배포 요청</CardTitle>
          <form onSubmit={handleSubmit} className="grid grid-cols-2 gap-4 md:grid-cols-5">
            <Field label="패키지 참조">
              <Input required value={packageRef} onChange={(e) => setPackageRef(e.target.value)} />
            </Field>
            <Field label="연결 ID(선택)">
              <Input value={connectionId} onChange={(e) => setConnectionId(e.target.value)} />
            </Field>
            <Field label="어댑터 유형">
              <Input required value={adapterType} onChange={(e) => setAdapterType(e.target.value)} />
            </Field>
            <Field label="샌드박스 계정 참조">
              <Input
                required
                value={providerSandboxAccountRef}
                onChange={(e) => setProviderSandboxAccountRef(e.target.value)}
              />
            </Field>
            <div className="col-span-2 flex items-end md:col-span-1">
              <Button type="submit" loading={requestDeployment.isPending} className="w-full">
                배포 요청
              </Button>
            </div>
          </form>
          {error !== null && (
            <div className="mt-3">
              <CommandError error={error} onRetry={() => void submitRequest()} />
            </div>
          )}
        </Card>

        <section className="space-y-4">
          <h2 className="text-lg font-medium text-fg">배포 목록</h2>
          {listIsError ? (
            <DeploymentsListError error={listError} onRetry={() => void refetch()} />
          ) : isLoading ? (
            <LoadingState />
          ) : deployments.length > 0 ? (
            <div className="grid grid-cols-2 gap-4">
              {deployments.map((deployment) => (
                <DeploymentRow key={deployment.id} deployment={deployment} />
              ))}
            </div>
          ) : (
            <EmptyState>페이퍼 배포가 없습니다.</EmptyState>
          )}
        </section>
      </div>
    </AppShell>
  );
}
