import { useListingSearch } from "@aios/shared-hooks";
import type { ListingSummary } from "@aios/shared-types";
import { Badge, Button, EmptyState, LoadingState, PageHeader, Select } from "@aios/ui-web";
import { useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { AppShell } from "../../components/layout/AppShell";

type SortBy = "RECOMMENDED" | "SHARPE_RATIO";

export function MarketplaceBrowsePage() {
  const [page, setPage] = useState(1);
  const [sortBy, setSortBy] = useState<SortBy>("RECOMMENDED");
  const { data, isLoading } = useListingSearch({ page, pageSize: 20, sortBy });
  const navigate = useNavigate();

  function openListing(listing: ListingSummary) {
    navigate(`/marketplace/${listing.id}`, { state: { listing } });
  }

  return (
    <AppShell>
      <div className="space-y-6">
        <PageHeader
          title="마켓플레이스"
          action={
            <Link to="/marketplace/sell">
              <Button type="button">내 전략 판매하기</Button>
            </Link>
          }
        />

        <div className="flex items-center gap-2 text-sm">
          <span className="text-fg-muted">정렬</span>
          <Select
            value={sortBy}
            onChange={(e) => {
              setSortBy(e.target.value as SortBy);
              setPage(1);
            }}
            className="w-40"
          >
            <option value="RECOMMENDED">추천순</option>
            <option value="SHARPE_RATIO">샤프비율순(랭킹)</option>
          </Select>
        </div>

        {isLoading ? (
          <LoadingState />
        ) : data && data.items.length > 0 ? (
          <div className="grid grid-cols-3 gap-4">
            {data.items.map((listing) => (
              <button
                key={listing.id}
                type="button"
                onClick={() => openListing(listing)}
                className="rounded-lg border border-border bg-surface p-4 text-left transition-colors hover:border-border-strong hover:bg-surface-hover"
              >
                <div className="flex items-center gap-2">
                  <p className="font-medium text-fg">{listing.strategyId}</p>
                  {listing.sellerType === "PLATFORM" && <Badge tone="accent">플랫폼</Badge>}
                </div>
                <p className="text-sm text-fg-muted">v{listing.strategyVersion}</p>
                <p className="tabular mt-2 text-lg font-semibold text-fg">
                  {listing.price ? `${listing.price} 크레딧` : "무료"}
                </p>
                {listing.sharpeRatio && (
                  <p className="tabular text-xs text-fg-muted">Sharpe {listing.sharpeRatio}</p>
                )}
              </button>
            ))}
          </div>
        ) : (
          <EmptyState>등록된 리스팅이 없습니다.</EmptyState>
        )}

        {data && data.total > data.pageSize && (
          <div className="flex justify-center gap-2">
            <Button
              type="button"
              variant="secondary"
              size="sm"
              disabled={page <= 1}
              onClick={() => setPage((p) => p - 1)}
            >
              이전
            </Button>
            <span className="flex items-center px-2 text-sm text-fg-muted">{page}</span>
            <Button
              type="button"
              variant="secondary"
              size="sm"
              disabled={page * data.pageSize >= data.total}
              onClick={() => setPage((p) => p + 1)}
            >
              다음
            </Button>
          </div>
        )}
      </div>
    </AppShell>
  );
}
