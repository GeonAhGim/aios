import { useConfirmPayment, usePendingPayments } from "@aios/shared-hooks";
import { Button, EmptyState, LoadingState, PageHeader } from "@aios/ui-web";
import { AppShell } from "../../components/layout/AppShell";

export function PendingPaymentsPage() {
  const { data, isLoading } = usePendingPayments();
  const confirm = useConfirmPayment();

  return (
    <AppShell>
      <div className="space-y-6">
        <PageHeader title="결제 대기 목록" />
        {isLoading ? (
          <LoadingState />
        ) : data && data.items.length > 0 ? (
          <ul className="space-y-3">
            {data.items.map((p) => (
              <li
                key={p.purchaseId}
                className="flex items-center justify-between rounded-lg border border-border bg-surface p-4"
              >
                <div>
                  <p className="font-medium text-fg">
                    구매 #{p.purchaseId} — {p.strategyId}@{p.strategyVersion}
                  </p>
                  <p className="tabular text-sm text-fg-muted">
                    {p.pricePaid ?? "가격 없음"} · {new Date(p.purchasedAt).toLocaleString()}
                  </p>
                </div>
                <Button
                  type="button"
                  className="!bg-success hover:!bg-success/90"
                  loading={confirm.isPending}
                  onClick={() =>
                    confirm.mutate({
                      purchaseId: p.purchaseId,
                      idempotencyKey: crypto.randomUUID(),
                    })
                  }
                >
                  입금 확인
                </Button>
              </li>
            ))}
          </ul>
        ) : (
          <EmptyState>대기 중인 결제가 없습니다.</EmptyState>
        )}
      </div>
    </AppShell>
  );
}
