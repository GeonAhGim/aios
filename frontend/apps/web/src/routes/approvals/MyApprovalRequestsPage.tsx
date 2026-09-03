import {
  useApproveMyRequest,
  useMyApprovalRequests,
  useRejectMyRequest,
} from "@aios/shared-hooks";
import { ApiError } from "@aios/api-client";
import { classifyForbidden, routeApiError, type ApprovalRequest } from "@aios/shared-types";
import { Badge, Button, EmptyState, LoadingState, PageHeader } from "@aios/ui-web";
import { useEffect, useState } from "react";
import { AppShell } from "../../components/layout/AppShell";
import { ErrorMessage } from "../../components/ErrorMessage";
import { ForbiddenNotice } from "../../components/ForbiddenNotice";

// FD-10.1 self-service — SOLO(본인 1인)와 DUAL의 첫 서명을 여기서 처리한다.
// DUAL 두 번째 서명자는 아직 신원 해석 로직이 없어(계정 연결 안 됨)
// 이 화면 대상이 아니다 — 관리자 경로로만 처리 가능(알려진 제약).
//
// spec §3.3 에러 taxonomy: 목록 조회 실패(task-1161)와 승인/거부 처리 실패(task-911)
// 모두 err.message를 직접 노출하지 않고 routeApiError(task-483)로 판정해 403/그 외를
// 각각 ForbiddenNotice/ErrorMessage 경로로만 보여준다. 재조회 가능한 kind(refetch_retry/
// backoff_retry)일 때만 onRetry를 노출한다 — 409(STATE_INVALID_TRANSITION, 이미 처리된
// 요청)는 자동 재시도 대상이 아니므로 재시도 버튼을 붙이지 않는다.
function RequestActionError({ error, onRetry }: { error: unknown; onRetry?: () => void }) {
  if (classifyForbidden(error)) return <ForbiddenNotice error={error} />;
  const routed = routeApiError(error);
  const canRetry = routed.kind === "refetch_retry" || routed.kind === "backoff_retry";
  return (
    <ErrorMessage
      errorCode={error instanceof ApiError ? error.errorCode : undefined}
      message={error instanceof Error ? error.message : undefined}
      traceId={error instanceof ApiError ? error.traceId : undefined}
      retryAfterSec={routed.kind === "backoff_retry" ? routed.afterSec : undefined}
      onRetry={canRetry ? onRetry : undefined}
    />
  );
}

function waitRemainingSeconds(request: ApprovalRequest): number {
  const readyAt = new Date(request.createdAt).getTime() + request.mandatoryWaitSeconds * 1000;
  return Math.max(0, Math.ceil((readyAt - Date.now()) / 1000));
}

function RequestCard({ request }: { request: ApprovalRequest }) {
  const approve = useApproveMyRequest();
  const reject = useRejectMyRequest();
  const [remaining, setRemaining] = useState(() => waitRemainingSeconds(request));
  const [error, setError] = useState<unknown>(null);

  useEffect(() => {
    if (remaining <= 0) return;
    const timer = setInterval(() => setRemaining(waitRemainingSeconds(request)), 1000);
    return () => clearInterval(timer);
  }, [request, remaining]);

  async function handle(action: "approve" | "reject") {
    setError(null);
    try {
      if (action === "approve") await approve.mutateAsync(request.id);
      else await reject.mutateAsync(request.id);
    } catch (err) {
      setError(err instanceof ApiError ? err : new Error("처리에 실패했습니다."));
    }
  }

  return (
    <li className="space-y-3 rounded-lg border border-border bg-surface p-4">
      <div className="flex items-center gap-2">
        <p className="font-medium text-fg">{request.requestedAction}</p>
        <Badge tone={request.scope === "PLATFORM" ? "warning" : "accent"}>
          {request.scope} · {request.approvalMode}
        </Badge>
      </div>
      <p className="text-xs text-fg-muted">
        요청 #{request.id} · {new Date(request.createdAt).toLocaleString()}
        {request.firstApproverId && " · 1차 서명 완료(2차 서명 대기)"}
      </p>
      {error !== null && <RequestActionError error={error} />}
      <div className="flex items-center gap-3">
        <Button
          type="button"
          className="!bg-success hover:!bg-success/90"
          disabled={remaining > 0}
          loading={approve.isPending}
          onClick={() => handle("approve")}
        >
          {remaining > 0 ? `승인 (${remaining}초 대기)` : "승인"}
        </Button>
        <Button
          type="button"
          variant="danger"
          loading={reject.isPending}
          onClick={() => handle("reject")}
        >
          거절
        </Button>
      </div>
    </li>
  );
}

export function MyApprovalRequestsPage() {
  const { data: requests, isLoading, isError, error, refetch } = useMyApprovalRequests();

  return (
    <AppShell>
      <div className="space-y-6">
        <PageHeader title="내 승인 대기 요청" />
        <p className="text-xs text-fg-muted">
          FD-10.1 — LIVE 실행 전환 등 리스크가 큰 조작은 강제 대기시간 이후 본인이
          직접 승인해야 진행됩니다.
        </p>
        {isError ? (
          <RequestActionError error={error} onRetry={() => refetch()} />
        ) : isLoading ? (
          <LoadingState />
        ) : requests && requests.length > 0 ? (
          <ul className="space-y-3">
            {requests.map((r) => (
              <RequestCard key={r.id} request={r} />
            ))}
          </ul>
        ) : (
          <EmptyState>대기 중인 승인 요청이 없습니다.</EmptyState>
        )}
      </div>
    </AppShell>
  );
}
