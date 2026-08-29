import { useApproveRequest, useRejectRequest } from "@aios/shared-hooks";
import { ApiError } from "@aios/api-client";
import { Alert, Button, Field, Input, PageHeader } from "@aios/ui-web";
import { useState } from "react";
import { useParams } from "react-router-dom";
import { AppShell } from "../../components/layout/AppShell";

// 알려진 제약 — 백엔드에 승인요청 "목록 조회" 엔드포인트가 아직 없어(FD-10.1
// 승인/거절만 존재) 요청 ID를 직접 입력받는다(알림 메일/푸시에 포함될
// 딥링크의 :requestId를 그대로 받는 형태로 설계 — FD-17 발송기가 아직
// 없어 실제 알림 본문에 링크가 나가진 않지만, 라우트 자체는 그 전제를
// 그대로 따른다).
export function AdminApprovalRequestPage() {
  const params = useParams<{ requestId?: string }>();
  const [requestId, setRequestId] = useState(params.requestId ?? "");
  const approve = useApproveRequest();
  const reject = useRejectRequest();
  const [result, setResult] = useState<{ status: string; requestedAction: string } | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function handleApprove() {
    setError(null);
    try {
      const r = await approve.mutateAsync(Number(requestId));
      setResult({ status: r.status, requestedAction: r.requestedAction });
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "승인에 실패했습니다.");
    }
  }

  async function handleReject() {
    setError(null);
    try {
      const r = await reject.mutateAsync(Number(requestId));
      setResult({ status: r.status, requestedAction: r.requestedAction });
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "거절에 실패했습니다.");
    }
  }

  return (
    <AppShell>
      <div className="max-w-md space-y-6">
        <PageHeader title="승인 요청 처리" />
        <p className="text-xs text-fg-muted">
          FD-10.1(LIVE 실행 승인) / FD-9.4b(Circuit Breaker 재가동 승인)이 동일 구조를 공유합니다.
        </p>
        <div className="space-y-4 rounded-lg border border-border bg-surface p-6">
          <Field label="승인요청 ID">
            <Input type="number" value={requestId} onChange={(e) => setRequestId(e.target.value)} />
          </Field>
          {error && <Alert>{error}</Alert>}
          {result && (
            <Alert tone="success">
              {result.requestedAction} → {result.status}
            </Alert>
          )}
          <div className="flex gap-3">
            <Button
              type="button"
              className="!bg-success hover:!bg-success/90"
              onClick={handleApprove}
              disabled={!requestId}
              loading={approve.isPending}
            >
              승인
            </Button>
            <Button
              type="button"
              variant="danger"
              onClick={handleReject}
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
