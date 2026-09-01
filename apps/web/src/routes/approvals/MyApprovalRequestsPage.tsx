import {
  useApproveMyRequest,
  useMyApprovalRequests,
  useRejectMyRequest,
} from "@aios/shared-hooks";
import { ApiError } from "@aios/api-client";
import type { ApprovalRequest } from "@aios/shared-types";
import { Alert, Badge, Button, EmptyState, LoadingState, PageHeader } from "@aios/ui-web";
import { useEffect, useState } from "react";
import { AppShell } from "../../components/layout/AppShell";

// FD-10.1 self-service — SOLO(본인 1인)와 DUAL의 첫 서명을 여기서 처리한다.
// DUAL 두 번째 서명자는 아직 신원 해석 로직이 없어(계정 연결 안 됨)
// 이 화면 대상이 아니다 — 관리자 경로로만 처리 가능(알려진 제약).
function waitRemainingSeconds(request: ApprovalRequest): number {
  const readyAt = new Date(request.createdAt).getTime() + request.mandatoryWaitSeconds * 1000;
  return Math.max(0, Math.ceil((readyAt - Date.now()) / 1000));
}

function RequestCard({ request }: { request: ApprovalRequest }) {
  const approve = useApproveMyRequest();
  const reject = useRejectMyRequest();
  const [remaining, setRemaining] = useState(() => waitRemainingSeconds(request));
  const [error, setError] = useState<string | null>(null);

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
      setError(err instanceof ApiError ? err.message : "처리에 실패했습니다.");
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
      {error && <Alert>{error}</Alert>}
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
  const { data: requests, isLoading } = useMyApprovalRequests();

  return (
    <AppShell>
      <div className="space-y-6">
        <PageHeader title="내 승인 대기 요청" />
        <p className="text-xs text-fg-muted">
          FD-10.1 — LIVE 실행 전환 등 리스크가 큰 조작은 강제 대기시간 이후 본인이
          직접 승인해야 진행됩니다.
        </p>
        {isLoading ? (
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
