import { useListingSearch } from "@aios/shared-hooks";
import type { ListingSummary } from "@aios/shared-types";
import { useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { AppShell } from "../../components/layout/AppShell";

export function MarketplaceBrowsePage() {
  const [page, setPage] = useState(1);
  const { data, isLoading } = useListingSearch({ page, pageSize: 20 });
  const navigate = useNavigate();

  function openListing(listing: ListingSummary) {
    navigate(`/marketplace/${listing.id}`, { state: { listing } });
  }

  return (
    <AppShell>
      <div className="space-y-6">
        <div className="flex items-center justify-between">
          <h1 className="text-2xl font-semibold text-slate-100">마켓플레이스</h1>
          <Link
            to="/marketplace/sell"
            className="rounded bg-slate-100 px-4 py-2 text-sm font-medium text-slate-950 hover:bg-white"
          >
            내 전략 판매하기
          </Link>
        </div>

        {isLoading ? (
          <p className="text-slate-500">불러오는 중...</p>
        ) : data && data.items.length > 0 ? (
          <div className="grid grid-cols-3 gap-4">
            {data.items.map((listing) => (
              <button
                key={listing.id}
                type="button"
                onClick={() => openListing(listing)}
                className="rounded-lg border border-slate-800 p-4 text-left hover:border-slate-600"
              >
                <p className="font-medium text-slate-100">{listing.strategyId}</p>
                <p className="text-sm text-slate-500">v{listing.strategyVersion}</p>
                <p className="mt-2 text-lg font-semibold text-slate-100">
                  {listing.price ? `${listing.price} USDT` : "가격 미정"}
                </p>
                {listing.sharpeRatio && (
                  <p className="text-xs text-slate-500">Sharpe {listing.sharpeRatio}</p>
                )}
              </button>
            ))}
          </div>
        ) : (
          <p className="text-slate-500">등록된 리스팅이 없습니다.</p>
        )}

        {data && data.total > data.pageSize && (
          <div className="flex justify-center gap-2">
            <button
              type="button"
              disabled={page <= 1}
              onClick={() => setPage((p) => p - 1)}
              className="rounded border border-slate-700 px-3 py-1 text-sm text-slate-300 disabled:opacity-40"
            >
              이전
            </button>
            <span className="text-sm text-slate-400">{page}</span>
            <button
              type="button"
              disabled={page * data.pageSize >= data.total}
              onClick={() => setPage((p) => p + 1)}
              className="rounded border border-slate-700 px-3 py-1 text-sm text-slate-300 disabled:opacity-40"
            >
              다음
            </button>
          </div>
        )}
      </div>
    </AppShell>
  );
}
