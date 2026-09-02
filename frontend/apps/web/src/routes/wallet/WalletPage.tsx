import { useRequestTopup, useWalletBalance } from "@aios/shared-hooks";
import { ApiError } from "@aios/api-client";
import { Alert, Button, Field, Input, PageHeader, Stat } from "@aios/ui-web";
import { useState, type FormEvent } from "react";
import { AppShell } from "../../components/layout/AppShell";
import { ErrorMessage } from "../../components/ErrorMessage";
import { DuplicateSubmitError, useIdempotentSubmit } from "../../hooks/useIdempotentSubmit";

export function WalletPage() {
  const { data: balance, isLoading } = useWalletBalance();
  const requestTopup = useRequestTopup();
  // NOTE: api-client의 requestTopup(POST /wallet/topup-requests)은 task-216 범위에서
  // postIdempotent로 이관되지 않아 Idempotency-Key 헤더를 아직 못 붙인다(api-client 미수정
  // 제약 — task-321 note 참조). 여기서는 키 수명주기 + in-flight 가드만 적용해 버튼
  // 연타로 인한 중복 제출은 막는다.
  const { submit } = useIdempotentSubmit("wallet.topup");
  const [amount, setAmount] = useState("30000");
  const [error, setError] = useState<ApiError | Error | null>(null);
  const [submitted, setSubmitted] = useState<{ id: number; amount: string } | null>(null);

  async function handleSubmit(e: FormEvent) {
    e.preventDefault();
    setError(null);
    setSubmitted(null);
    try {
      const result = await submit(() => requestTopup.mutateAsync({ amount }));
      setSubmitted({ id: result.id, amount: result.requestedAmount });
    } catch (err) {
      if (err instanceof DuplicateSubmitError) return;
      setError(err instanceof Error ? err : new Error("충전 요청에 실패했습니다."));
    }
  }

  return (
    <AppShell>
      <div className="max-w-md space-y-6">
        <PageHeader title="지갑" />
        <Stat
          label="보유 크레딧"
          value={isLoading ? "…" : `${balance?.balance ?? "0"} 크레딧`}
        />

        <form
          onSubmit={handleSubmit}
          className="space-y-3 rounded-lg border border-border bg-surface p-6"
        >
          <Field label="충전 신청 금액 (크레딧, 1크레딧 = 1원)">
            <Input
              type="number"
              step="1"
              min="1"
              required
              value={amount}
              onChange={(e) => setAmount(e.target.value)}
            />
          </Field>
          <p className="text-xs text-fg-muted">
            신청 후 실제 계좌로 입금하면 관리자 확인 즉시 크레딧이 반영됩니다.
          </p>
          {error && <ErrorMessage traceId={error instanceof ApiError ? error.traceId : null} />}
          {submitted && (
            <Alert tone="success">
              충전 요청 #{submitted.id} 접수됨 ({submitted.amount} 크레딧) — 관리자 확인 대기 중
            </Alert>
          )}
          <Button type="submit" loading={requestTopup.isPending} className="w-full">
            충전 신청
          </Button>
        </form>
      </div>
    </AppShell>
  );
}
