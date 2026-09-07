// ChartToolbar.tsx와 동일한 이유로 배럴 대신 vendor에 의존하지 않는
// indicators/overlayRegistry 서브모듈만 직접 불러온다.
import type { OverlayEntry } from "@aios/chart-engine/src/indicators/overlayRegistry";
import {
  ApiError,
  createIndicatorsClient,
  type IndicatorCatalogItem,
  type IndicatorTier,
  type ListIndicatorsParams,
  type ListIndicatorsResult,
} from "@aios/api-client";
import { useAuthStore } from "@aios/shared-hooks";
import { routeApiError } from "@aios/shared-types";
import { Button, EmptyState, Input, LoadingState, Select } from "@aios/ui-web";
import { useInfiniteQuery } from "@tanstack/react-query";
import type { KeyboardEvent, ReactNode } from "react";
import { useEffect, useMemo, useRef, useState } from "react";
import { ErrorMessage } from "../../components/ErrorMessage";

// IND-14: CH-6a가 로컬 overlayRegistry 목록만 보여주던 자리를, IND-12(task-1730)
// `GET /v1/indicators` 코어·OSS·스크립트 3층 카탈로그로 실배선한다(탐색·검색·
// 커서 페이지네이션). 서버는 tier별 조회를 지원하지 않으므로(registry_tiers.py
// list_catalog는 q/category만 받고 이름순 정렬 전체를 돌려준다) tier 탭은 이미
// 받아온 페이지를 프론트에서 걸러 보여주는 것뿐이다 — 새 서버 계약을 상상하지
// 않는다. 값 계산·즉시 미리보기는 여전히 이 컴포넌트 소관이 아니다(스크립트
// 지표 미리보기는 CH-12/task-1808, 즐겨찾기는 서버 저장 계약이 아직 없다 — 결정
// 문서 참고). 선택은 available(overlayRegistry) 매칭 여부와 무관하게 id만
// 부모(ChartPage)로 올려보낸다 — 실제 렌더 가능 여부는 ChartPage의 overlayEntries
// 교차 필터가 이미 담당한다(선택했지만 그릴 수 없는 지표는 조용히 무시된다).
export type ListIndicators = (params?: ListIndicatorsParams) => Promise<ListIndicatorsResult>;

interface IndicatorPickerProps {
  available: readonly OverlayEntry[];
  selectedIds: readonly string[];
  onToggle: (id: string) => void;
  /** 테스트 주입용. 기본값은 실제 GET /v1/indicators 클라이언트. */
  listIndicators?: ListIndicators;
}

const LISTBOX_ID = "indicator-picker-listbox";
const SEARCH_DEBOUNCE_MS = 300;

const baseUrl = import.meta.env.VITE_API_BASE_URL ?? "http://localhost:8000";
const defaultIndicatorsClient = createIndicatorsClient(baseUrl, () => useAuthStore.getState().token);

type TierFilter = "all" | IndicatorTier;

const TIER_OPTIONS: readonly { value: TierFilter; label: string }[] = [
  { value: "all", label: "전체" },
  { value: "core", label: "코어" },
  { value: "oss", label: "OSS" },
  { value: "script", label: "스크립트" },
];

const TIER_LABEL: Record<IndicatorTier, string> = { core: "코어", oss: "OSS", script: "스크립트" };

function placementLabel(available: readonly OverlayEntry[], item: IndicatorCatalogItem): string | null {
  const entry = available.find((e) => e.id === item.name);
  if (!entry) return null;
  return entry.placement === "main-overlay" ? "메인" : "서브패널";
}

export function IndicatorPicker({
  available,
  selectedIds,
  onToggle,
  listIndicators = defaultIndicatorsClient.listIndicators,
}: IndicatorPickerProps) {
  const [open, setOpen] = useState(false);
  const [activeIndex, setActiveIndex] = useState(0);
  const [searchInput, setSearchInput] = useState("");
  const [debouncedQuery, setDebouncedQuery] = useState("");
  const [tierFilter, setTierFilter] = useState<TierFilter>("all");
  const containerRef = useRef<HTMLDivElement>(null);
  const listRef = useRef<HTMLUListElement>(null);

  useEffect(() => {
    const timer = setTimeout(() => setDebouncedQuery(searchInput.trim()), SEARCH_DEBOUNCE_MS);
    return () => clearTimeout(timer);
  }, [searchInput]);

  const query = useInfiniteQuery({
    queryKey: ["indicator-picker", debouncedQuery],
    queryFn: ({ pageParam }: { pageParam: string | undefined }) =>
      listIndicators({ q: debouncedQuery || undefined, cursor: pageParam }),
    initialPageParam: undefined as string | undefined,
    getNextPageParam: (lastPage) => lastPage.nextCursor ?? undefined,
    enabled: open,
  });

  const allItems = useMemo(() => query.data?.pages.flatMap((p) => p.items) ?? [], [query.data]);
  const visibleItems = useMemo(
    () => (tierFilter === "all" ? allItems : allItems.filter((item) => item.tier === tierFilter)),
    [allItems, tierFilter],
  );

  useEffect(() => {
    setActiveIndex(0);
  }, [debouncedQuery, tierFilter]);

  useEffect(() => {
    if (open && !query.isLoading && !query.isError) listRef.current?.focus();
  }, [open, query.isLoading, query.isError]);

  function close(): void {
    setOpen(false);
    containerRef.current?.querySelector<HTMLButtonElement>("button")?.focus();
  }

  function moveActive(delta: number): void {
    if (visibleItems.length === 0) return;
    setActiveIndex((prev) => (prev + delta + visibleItems.length) % visibleItems.length);
  }

  function onListKeyDown(event: KeyboardEvent<HTMLUListElement>): void {
    switch (event.key) {
      case "ArrowDown":
        event.preventDefault();
        moveActive(1);
        break;
      case "ArrowUp":
        event.preventDefault();
        moveActive(-1);
        break;
      case "Home":
        event.preventDefault();
        setActiveIndex(0);
        break;
      case "End":
        event.preventDefault();
        setActiveIndex(Math.max(0, visibleItems.length - 1));
        break;
      case "Enter":
      case " ": {
        event.preventDefault();
        const entry = visibleItems[activeIndex];
        if (entry) onToggle(entry.name);
        break;
      }
      case "Escape":
        event.preventDefault();
        close();
        break;
      default:
        break;
    }
  }

  const routed = query.error ? routeApiError(query.error) : null;
  const canRetry = routed?.kind === "refetch_retry" || routed?.kind === "backoff_retry";

  let body: ReactNode;
  if (query.isError) {
    body = (
      <ErrorMessage
        errorCode={query.error instanceof ApiError ? query.error.errorCode : undefined}
        message={query.error instanceof Error ? query.error.message : undefined}
        traceId={query.error instanceof ApiError ? query.error.traceId : undefined}
        retryAfterSec={routed?.kind === "backoff_retry" ? routed.afterSec : undefined}
        onRetry={canRetry ? () => query.refetch() : undefined}
      />
    );
  } else if (query.isLoading) {
    body = <LoadingState />;
  } else if (visibleItems.length === 0) {
    body = <EmptyState>지표가 없습니다.</EmptyState>;
  } else {
    body = (
      <ul
        id={LISTBOX_ID}
        role="listbox"
        aria-multiselectable="true"
        aria-label="지표 목록"
        aria-activedescendant={visibleItems[activeIndex] ? `indicator-option-${visibleItems[activeIndex].name}` : undefined}
        tabIndex={0}
        className="max-h-64 overflow-auto"
        onKeyDown={onListKeyDown}
        ref={listRef}
      >
        {visibleItems.map((item, index) => {
          const selected = selectedIds.includes(item.name);
          const placement = placementLabel(available, item);
          return (
            <li
              key={item.name}
              id={`indicator-option-${item.name}`}
              role="option"
              aria-selected={selected}
              data-active={index === activeIndex || undefined}
              className={
                "cursor-pointer rounded px-2 py-1.5 text-sm " +
                (index === activeIndex ? "bg-surface-hover" : "") +
                (selected ? " font-medium text-accent" : " text-fg")
              }
              onMouseEnter={() => setActiveIndex(index)}
              onClick={() => onToggle(item.name)}
            >
              {selected ? "✓ " : ""}
              {item.name}
              <span className="ml-1 text-xs text-fg-muted">
                ({TIER_LABEL[item.tier]} · {placement ?? item.category})
              </span>
            </li>
          );
        })}
      </ul>
    );
  }

  return (
    <div className="relative inline-block" data-testid="indicator-picker" ref={containerRef}>
      <Button
        type="button"
        variant="secondary"
        size="sm"
        aria-haspopup="listbox"
        aria-expanded={open}
        aria-controls={LISTBOX_ID}
        onClick={() => setOpen((prev) => !prev)}
      >
        지표 선택 ({selectedIds.length})
      </Button>

      {open && (
        <div className="absolute z-10 mt-1 w-72 rounded-md border border-border bg-surface p-2 shadow-lg">
          <div className="mb-2 flex gap-1.5">
            <Input
              aria-label="지표 검색"
              placeholder="이름 검색"
              value={searchInput}
              onChange={(e) => setSearchInput(e.target.value)}
              className="flex-1"
            />
            <Select
              aria-label="지표 계층 필터"
              value={tierFilter}
              onChange={(e) => setTierFilter(e.target.value as TierFilter)}
              className="w-28 flex-none"
            >
              {TIER_OPTIONS.map((opt) => (
                <option key={opt.value} value={opt.value}>
                  {opt.label}
                </option>
              ))}
            </Select>
          </div>
          {body}
          {query.hasNextPage && !query.isError && (
            <Button
              type="button"
              variant="ghost"
              size="sm"
              className="mt-2 w-full"
              disabled={query.isFetchingNextPage}
              onClick={() => query.fetchNextPage()}
            >
              {query.isFetchingNextPage ? "불러오는 중..." : "더 보기"}
            </Button>
          )}
        </div>
      )}

      {selectedIds.length > 0 && (
        <ul className="mt-2 flex flex-wrap gap-1.5" aria-label="선택된 지표">
          {selectedIds.map((id) => (
            <li key={id}>
              <Button type="button" variant="ghost" size="sm" onClick={() => onToggle(id)}>
                {id} ✕
              </Button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
