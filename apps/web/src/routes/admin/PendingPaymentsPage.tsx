import { useConfirmPayment, usePendingPayments } from "@aios/shared-hooks";
import { AppShell } from "../../components/layout/AppShell";

export function PendingPaymentsPage() {
  const { data, isLoading } = usePendingPayments();
  const confirm = useConfirmPayment();

  return (
    <AppShell>
      <div className="space-y-6">
        <h1 className="text-2xl font-semibold text-slate-100">결제 대기 목록</h1>
        {isLoading ? (
          <p className="text-slate-500">불러오는 중...</p>
        ) : data && data.items.length > 0 ? (
          <ul className="space-y-3">
            {data.items.map((p) => (
              <li
                key={p.purchaseId}
                className="flex items-center justify-between rounded-lg border border-slate-800 p-4"
              >
                <div>
                  <p className="font-medium text-slate-100">
                    구매 #{p.purchaseId} — {p.strategyId}@{p.strategyVersion}
                  </p>
                  <p className="text-sm text-slate-500">
                    {p.pricePaid ?? "가격 없음"} · {new Date(p.purchasedAt).toLocaleString()}
                  </p>
                </div>
                <button
                  type="button"
                  onClick={() =>
                    confirm.mutate({
                      purchaseId: p.purchaseId,
                      idempotencyKey: crypto.randomUUID(),
                    })
                  }
                  disabled={confirm.isPending}
                  className="rounded bg-emerald-700 px-3 py-1 text-sm text-white hover:bg-emerald-600 disabled:opacity-50"
                >
                  입금 확인
                </button>
              </li>
            ))}
          </ul>
        ) : (
          <p className="text-slate-500">대기 중인 결제가 없습니다.</p>
        )}
      </div>
    </AppShell>
  );
}
