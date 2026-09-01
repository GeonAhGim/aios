import { useConfirmTopup, usePendingTopups } from "@aios/shared-hooks";
import { Button, EmptyState, LoadingState, PageHeader } from "@aios/ui-web";
import { AppShell } from "../../components/layout/AppShell";

export function WalletTopupsPage() {
  const { data, isLoading } = usePendingTopups();
  const confirm = useConfirmTopup();

  return (
    <AppShell>
      <div className="space-y-6">
        <PageHeader title="충전 요청 대기 목록" />
        {isLoading ? (
          <LoadingState />
        ) : data && data.items.length > 0 ? (
          <ul className="space-y-3">
            {data.items.map((t) => (
              <li
                key={t.id}
                className="flex items-center justify-between rounded-lg border border-border bg-surface p-4"
              >
                <div>
                  <p className="font-medium text-fg">충전요청 #{t.id}</p>
                  <p className="tabular text-sm text-fg-muted">
                    {t.requestedAmount} 크레딧 · {new Date(t.requestedAt).toLocaleString()}
                  </p>
                </div>
                <Button
                  type="button"
                  className="!bg-success hover:!bg-success/90"
                  loading={confirm.isPending}
                  onClick={() =>
                    confirm.mutate({
                      topupId: t.id,
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
          <EmptyState>대기 중인 충전 요청이 없습니다.</EmptyState>
        )}
      </div>
    </AppShell>
  );
}
