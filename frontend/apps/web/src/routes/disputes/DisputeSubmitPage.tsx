import { useSubmitDispute } from "@aios/shared-hooks";
import { ApiError } from "@aios/api-client";
import { Alert, Button, Field, Input, PageHeader, Textarea } from "@aios/ui-web";
import { useState, type FormEvent } from "react";
import { AppShell } from "../../components/layout/AppShell";

export function DisputeSubmitPage() {
  const [purchaseId, setPurchaseId] = useState("");
  const [reason, setReason] = useState("");
  const [error, setError] = useState<string | null>(null);
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
      setError(err instanceof ApiError ? err.message : "분쟁 신고에 실패했습니다.");
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
            {error && <Alert>{error}</Alert>}
            <Button type="submit" loading={submitDispute.isPending} className="w-full">
              분쟁 신고 제출
            </Button>
          </form>
        )}
      </div>
    </AppShell>
  );
}
