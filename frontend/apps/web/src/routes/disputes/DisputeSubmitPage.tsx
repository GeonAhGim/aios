import { useSubmitDispute } from "@aios/shared-hooks";
import { ApiError } from "@aios/api-client";
import { classifyBadRequest, classifyForbidden, routeApiError } from "@aios/shared-types";
import { Alert, Button, Field, Input, PageHeader, Textarea } from "@aios/ui-web";
import { useState, type FormEvent } from "react";
import { AppShell } from "../../components/layout/AppShell";
import { BadRequestNotice } from "../../components/BadRequestNotice";
import { ErrorMessage } from "../../components/ErrorMessage";
import { ForbiddenNotice } from "../../components/ForbiddenNotice";

// spec §3.3 에러 taxonomy: 분쟁 신고 실패는 err.message를 직접 노출하지 않고
// routeApiError(task-483)로 판정해 400/403/그 외를 각각 BadRequestNotice/
// ForbiddenNotice/ErrorMessage 경로로만 보여준다(task-901 패턴).
function SubmitDisputeError({ error }: { error: unknown }) {
  if (classifyBadRequest(error)) return <BadRequestNotice error={error} />;
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

export function DisputeSubmitPage() {
  const [purchaseId, setPurchaseId] = useState("");
  const [reason, setReason] = useState("");
  const [error, setError] = useState<unknown>(null);
  const [submitted, setSubmitted] = useState<{ disputeId: number; status: string } | null>(null);
  const submitDispute = useSubmitDispute();

  async function handleSubmit(e: FormEvent) {
    e.preventDefault();
    setError(null);
    try {
      const result = await submitDispute.mutateAsync({
        purchaseId: Number(purchaseId),
        reason,
      });
      setSubmitted({ disputeId: result.disputeId, status: result.status });
    } catch (err) {
      setError(err instanceof ApiError ? err : new Error("분쟁 신고에 실패했습니다."));
    }
  }

  return (
    <AppShell>
      <div className="max-w-md space-y-6">
        <PageHeader title="분쟁 신고" />
        {submitted ? (
          <Alert tone="success">
            분쟁이 접수됐습니다 (#{submitted.disputeId}, {submitted.status}).
          </Alert>
        ) : (
          <form onSubmit={handleSubmit} className="space-y-3 rounded-lg border border-border bg-surface p-6">
            <Field label="구매 ID">
              <Input
                type="number"
                required
                value={purchaseId}
                onChange={(e) => setPurchaseId(e.target.value)}
              />
            </Field>
            <Field label="사유">
              <Textarea required value={reason} onChange={(e) => setReason(e.target.value)} rows={4} />
            </Field>
            {error !== null && <SubmitDisputeError error={error} />}
            <Button type="submit" loading={submitDispute.isPending} className="w-full">
              분쟁 신고 제출
            </Button>
          </form>
        )}
      </div>
    </AppShell>
  );
}
