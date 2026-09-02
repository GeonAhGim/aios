import type { ApiResponsePageMeta } from "@aios/api-client";
import { useListingSearch } from "@aios/shared-hooks";
import type { ListingSummary } from "@aios/shared-types";
import {
  Badge,
  Button,
  EmptyState,
  LoadingState,
  PageHeader,
  Select,
} from "@aios/ui-web";
import { useCallback, useEffect, useState } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import { AppShell } from "../../components/layout/AppShell";
import { Pagination } from "../../components/Pagination";
import { derivePageState } from "../../lib/pagination";

type SortBy = "RECOMMENDED" | "SHARPE_RATIO";

const DEFAULT_PAGE_SIZE = 20;

export function MarketplaceBrowsePage() {
  const [searchParams, setSearchParams] = useSearchParams();
  const [sortBy, setSortBy] = useState<SortBy>("RECOMMENDED");
  const page = Math.max(1, Number(searchParams.get("page")) || 1);
  const size = Math.max(
    1,
    Number(searchParams.get("size")) || DEFAULT_PAGE_SIZE,
  );
  const { data, isLoading } = useListingSearch({
    page,
    pageSize: size,
    sortBy,
  });
  const navigate = useNavigate();

  const meta: ApiResponsePageMeta | null = data
    ? {
        total: data.total,
        page: data.page,
        size: data.pageSize,
        next_cursor: null,
      }
    : null;
  const pageState = derivePageState(meta, { defaultSize: DEFAULT_PAGE_SIZE });

  const goToPage = useCallback(
    (nextPage: number) => {
      setSearchParams(
        (prev) => {
          const next = new URLSearchParams(prev);
          next.set("page", String(nextPage));
          next.set("size", String(size));
          return next;
        },
        { replace: true },
      );
    },
    [setSearchParams, size],
  );

  // 마지막 페이지를 넘어선 요청(예: 새로고침 전 즐겨찾은 URL)은 derivePageState가
  // 안전한 페이지로 클램프해 주므로, 그 값으로 URL을 되돌려 실제 아이템을 다시 받아온다.
  useEffect(() => {
    if (
      data &&
      pageState.totalPages !== null &&
      pageState.totalPages > 0 &&
      pageState.page !== page
    ) {
      goToPage(pageState.page);
    }
  }, [data, pageState.page, pageState.totalPages, page, goToPage]);

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
              goToPage(1);
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
                  {listing.sellerType === "PLATFORM" && (
                    <Badge tone="accent">플랫폼</Badge>
                  )}
                </div>
                <p className="text-sm text-fg-muted">
                  v{listing.strategyVersion}
                </p>
                <p className="tabular mt-2 text-lg font-semibold text-fg">
                  {listing.price ? `${listing.price} 크레딧` : "무료"}
                </p>
                {listing.sharpeRatio && (
                  <p className="tabular text-xs text-fg-muted">
                    Sharpe {listing.sharpeRatio}
                  </p>
                )}
              </button>
            ))}
          </div>
        ) : (
          <EmptyState>등록된 리스팅이 없습니다.</EmptyState>
        )}

        {data && <Pagination state={pageState} onPageChange={goToPage} />}
      </div>
    </AppShell>
  );
}
