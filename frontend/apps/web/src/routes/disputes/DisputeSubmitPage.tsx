import { useSubmitDispute } from "@aios/shared-hooks";
import { ApiError } from "@aios/api-client";
import { classifyBadRequest, classifyForbidden, routeApiError } from "@aios/shared-types";
import { Alert, Button, Field, Input, PageHeader, Textarea } from "@aios/ui-web";
import { useState, type FormEvent } from "react";
import { AppShell } from "../../components/layout/AppShell";
import { BadRequestNotice } from "../../components/BadRequestNotice";
import { ErrorMessage } from "../../components/ErrorMessage";
import { ForbiddenNotice } from "../../components/ForbiddenNotice";
import { useFieldErrors } from "../../hooks/useFieldErrors";

// spec §3.3 에러 taxonomy: 분쟁 신고 실패는 err.message를 직접 노출하지 않고
// routeApiError(task-483)로 판정해 400/403/그 외를 각각 BadRequestNotice/
// ForbiddenNotice/ErrorMessage 경로로만 보여준다(task-901 패턴).
//
// task-954: VALIDATION_INVALID_FIELD는 classifyBadRequest가 "field"로 분류해
// BadRequestNotice가 자체적으로 null을 렌더한다(task-364 설계) — 그래서 지금까지
// 이 경로는 배너도 인라인도 없이 완전히 조용했다. fieldErrors를 ErrorMessage에
// 넘겨 계약(비어있지 않으면 배너 생략)을 지키고, 실제 표시는 아래 입력 옆
// Field.error로 한다.
function SubmitDisputeError({ error, fieldErrors }: { error: unknown; fieldErrors: Record<string, string> }) {
  if (classifyBadRequest(error)) return <BadRequestNotice error={error} />;
  if (classifyForbidden(error)) return <ForbiddenNotice error={error} />;
  const routed = routeApiError(error);
  return (
    <ErrorMessage
      errorCode={error instanceof ApiError ? error.errorCode : undefined}
      message={error instanceof Error ? error.message : undefined}
      traceId={error instanceof ApiError ? error.traceId : undefined}
      retryAfterSec={routed.kind === "backoff_retry" ? routed.afterSec : undefined}
      fieldErrors={fieldErrors}
    />
  );
}

export function DisputeSubmitPage() {
  const [purchaseId, setPurchaseId] = useState("");
  const [reason, setReason] = useState("");
  const [error, setError] = useState<unknown>(null);
  const [submitted, setSubmitted] = useState<{ disputeId: number; status: string } | null>(null);
  const submitDispute = useSubmitDispute();
  const { fieldErrors, setFromError, clearField } = useFieldErrors();

  async function handleSubmit(e: FormEvent) {
    e.preventDefault();
    setError(null);
    setFromError(null);
    try {
      const result = await submitDispute.mutateAsync({
        purchaseId: Number(purchaseId),
        reason,
      });
      setSubmitted({ disputeId: result.disputeId, status: result.status });
    } catch (err) {
      setError(err instanceof ApiError ? err : new Error("분쟁 신고에 실패했습니다."));
      setFromError(err);
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
            <Field label="구매 ID" error={fieldErrors.purchase_id}>
              <Input
                type="number"
                required
                value={purchaseId}
                onChange={(e) => {
                  setPurchaseId(e.target.value);
                  clearField("purchase_id");
                }}
              />
            </Field>
            <Field label="사유" error={fieldErrors.reason}>
              <Textarea
                required
                value={reason}
                onChange={(e) => {
                  setReason(e.target.value);
                  clearField("reason");
                }}
                rows={4}
              />
            </Field>
            {error !== null && <SubmitDisputeError error={error} fieldErrors={fieldErrors} />}
            <Button type="submit" loading={submitDispute.isPending} className="w-full">
              분쟁 신고 제출
            </Button>
          </form>
        )}
      </div>
    </AppShell>
  );
}
