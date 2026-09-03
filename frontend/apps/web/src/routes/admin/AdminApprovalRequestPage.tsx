import {
  useApproveRequest,
  usePendingApprovalRequests,
  useRejectRequest,
} from "@aios/shared-hooks";
import { ApiError } from "@aios/api-client";
import { classifyForbidden, routeApiError } from "@aios/shared-types";
import { Alert, Badge, Button, EmptyState, Field, Input, LoadingState, PageHeader } from "@aios/ui-web";
import { useState } from "react";
import { useParams } from "react-router-dom";
import { AppShell } from "../../components/layout/AppShell";
import { ErrorMessage } from "../../components/ErrorMessage";
import { ForbiddenNotice } from "../../components/ForbiddenNotice";

// FD-10.1(LIVE 실행 승인) / FD-9.4b(Circuit Breaker 재가동 승인)이 동일
// 구조를 공유한다. PLATFORM 범위(user_id 없음, 예: Circuit Breaker
// 재가동)는 본질적으로 관리자만 처리할 수 있어 이 화면에 남는다 —
// USER 범위(SOLO/DUAL 본인 서명)는 /approval-requests(self-service)로
// 옮겨갔고, 여기서는 관리자 개입이 실제로 필요한 경우의 오버라이드
// 용도로만 유지한다. 목록에 없는 요청은 하단의 수동 ID 입력으로 처리.
//
// spec §3.3 에러 taxonomy: 처리 실패는 err.message를 직접 노출하지 않고
// routeApiError(task-483)로 판정해 403/그 외를 각각 ForbiddenNotice/
// ErrorMessage 경로로만 보여준다(task-911).
function ApprovalActionError({ error }: { error: unknown }) {
  if (classifyForbidden(error)) return <ForbiddenNotice error={error} />;
  const routed = routeApiError(error);
  return (
    <ErrorMessage
      errorCode={error instanceof ApiError ? error.errorCode : undefined}
      message={error instanceof Error ? error.message : undefined}
      traceId={error instanceof ApiError ? error.traceId : undefined}
      retryAfterSec={routed.kind === "backoff_retry" ? routed.afterSec : undefined}
    />
  );
}

export function AdminApprovalRequestPage() {
  const params = useParams<{ requestId?: string }>();
  const { data: pending, isLoading } = usePendingApprovalRequests();
  const [requestId, setRequestId] = useState(params.requestId ?? "");
  const approve = useApproveRequest();
  const reject = useRejectRequest();
  const [result, setResult] = useState<{ status: string; requestedAction: string } | null>(null);
  const [error, setError] = useState<unknown>(null);

  async function handle(action: "approve" | "reject", id: string) {
    setError(null);
    try {
      const r = action === "approve"
        ? await approve.mutateAsync(Number(id))
        : await reject.mutateAsync(Number(id));
      setResult({ status: r.status, requestedAction: r.requestedAction });
    } catch (err) {
      setError(err instanceof ApiError ? err : new Error("처리에 실패했습니다."));
    }
  }

  return (
    <AppShell>
      <div className="max-w-md space-y-6">
        <PageHeader title="승인 요청 처리" />
        {error !== null && <ApprovalActionError error={error} />}
        {result && (
          <Alert tone="success">
            {result.requestedAction} → {result.status}
          </Alert>
        )}

        {isLoading ? (
          <LoadingState />
        ) : pending && pending.length > 0 ? (
          <ul className="space-y-3">
            {pending.map((r) => (
              <li key={r.id} className="space-y-3 rounded-lg border border-border bg-surface p-4">
                <div className="flex items-center gap-2">
                  <p className="font-medium text-fg">
                    #{r.id} {r.requestedAction}
                  </p>
                  <Badge tone="warning">
                    {r.scope} · {r.approvalMode}
                  </Badge>
                </div>
                <div className="flex gap-3">
                  <Button
                    type="button"
                    className="!bg-success hover:!bg-success/90"
                    size="sm"
                    onClick={() => handle("approve", String(r.id))}
                    loading={approve.isPending}
                  >
                    승인
                  </Button>
                  <Button
                    type="button"
                    variant="danger"
                    size="sm"
                    onClick={() => handle("reject", String(r.id))}
                    loading={reject.isPending}
                  >
                    거절
                  </Button>
                </div>
              </li>
            ))}
          </ul>
        ) : (
          <EmptyState>대기 중인 승인 요청이 없습니다.</EmptyState>
        )}

        <div className="space-y-3 rounded-lg border border-border-strong bg-bg p-4">
          <p className="text-xs text-fg-muted">목록에 없는 요청 ID를 직접 처리</p>
          <Field label="승인요청 ID">
            <Input type="number" value={requestId} onChange={(e) => setRequestId(e.target.value)} />
          </Field>
          <div className="flex gap-3">
            <Button
              type="button"
              variant="secondary"
              size="sm"
              onClick={() => handle("approve", requestId)}
              disabled={!requestId}
              loading={approve.isPending}
            >
              승인
            </Button>
            <Button
              type="button"
              variant="secondary"
              size="sm"
              onClick={() => handle("reject", requestId)}
              disabled={!requestId}
              loading={reject.isPending}
            >
              거절
            </Button>
          </div>
        </div>
      </div>
    </AppShell>
  );
}
