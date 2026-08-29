import { useSubmitDispute } from "@aios/shared-hooks";
import { ApiError } from "@aios/api-client";
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
        <h1 className="text-2xl font-semibold text-slate-100">분쟁 신고</h1>
        {submitted ? (
          <div className="rounded border border-emerald-900 bg-emerald-950/30 p-4 text-sm text-emerald-300">
            분쟁이 접수됐습니다 (#{submitted.disputeId}, {submitted.status}).
          </div>
        ) : (
          <form onSubmit={handleSubmit} className="space-y-3">
            <div className="space-y-1">
              <label className="text-sm text-slate-400">구매 ID</label>
              <input
                type="number"
                required
                value={purchaseId}
                onChange={(e) => setPurchaseId(e.target.value)}
                className="w-full rounded border border-slate-700 bg-slate-900 px-3 py-2 text-slate-100"
              />
            </div>
            <div className="space-y-1">
              <label className="text-sm text-slate-400">사유</label>
              <textarea
                required
                value={reason}
                onChange={(e) => setReason(e.target.value)}
                rows={4}
                className="w-full rounded border border-slate-700 bg-slate-900 px-3 py-2 text-slate-100"
              />
            </div>
            {error && <p className="text-sm text-red-400">{error}</p>}
            <button
              type="submit"
              disabled={submitDispute.isPending}
              className="w-full rounded bg-slate-100 px-3 py-2 font-medium text-slate-950 hover:bg-white disabled:opacity-50"
            >
              {submitDispute.isPending ? "제출 중..." : "분쟁 신고 제출"}
            </button>
          </form>
        )}
      </div>
    </AppShell>
  );
}
