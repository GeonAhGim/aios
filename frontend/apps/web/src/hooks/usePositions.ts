import {
  createPositionsClient,
  type NavSeriesParams,
  type NavSeriesResult,
  type PositionJournalResult,
  type PositionListParams,
  type PositionListResult,
  type PositionsClient,
} from "@aios/api-client";
import { useAuthStore } from "@aios/shared-hooks";
import { useQuery, type UseQueryResult } from "@tanstack/react-query";
import { useMemo } from "react";

// task-1524(LB-19): positions 조회 3종(list/journal/nav)을 TanStack Query에 얹는 훅.
// 클라이언트는 api-client createPositionsClient(apiPaths.ts positions.* 경로)이고,
// 화면은 테스트 주입을 위해 client를 넘길 수 있다(InstrumentsPage의 marketDataClient
// 관용). 낙관적 갱신·캐시 수동 갱신은 없다 — 포지션·저널·NAV의 SSOT는 서버다
// (task-709 decision). 에러는 여기서 잡지 않고 query.error로 그대로 노출해 화면이
// routeApiError로 분류한다(새 분류기 금지).
export type PositionsClientLike = Pick<PositionsClient, "listPositions" | "getPositionJournal" | "getNavSeries">;

const baseUrl = import.meta.env.VITE_API_BASE_URL ?? "http://localhost:8000";

export function usePositionsClient(injected?: PositionsClientLike): PositionsClientLike {
  return useMemo(
    () => injected ?? createPositionsClient(baseUrl, () => useAuthStore.getState().token),
    [injected],
  );
}

export const positionsQueryKeys = {
  list: (params: PositionListParams) =>
    ["positions", "list", params.accountId ?? null, params.instrumentId ?? null] as const,
  // cursor는 서버가 준 불투명 문자열 그대로 키에 넣는다(숫자 변환 금지).
  journal: (positionKey: string, cursor: string | undefined, limit: number | undefined) =>
    ["positions", "journal", positionKey, cursor ?? null, limit ?? null] as const,
  nav: (params: NavSeriesParams) =>
    ["positions", "nav", params.accountId, params.startDate, params.endDate] as const,
};

export function usePositionList(
  client: Pick<PositionsClientLike, "listPositions">,
  params: PositionListParams = {},
): UseQueryResult<PositionListResult> {
  return useQuery({
    queryKey: positionsQueryKeys.list(params),
    queryFn: () => client.listPositions(params),
  });
}

export interface UsePositionJournalOptions {
  /** 이전 페이지 응답의 nextCursor(useCursorPage.cursor). 첫 페이지면 undefined. */
  cursor?: string;
  limit?: number;
  /** false면 요청하지 않는다(저널 패널이 접혀 있을 때). */
  enabled?: boolean;
}

export function usePositionJournal(
  client: Pick<PositionsClientLike, "getPositionJournal">,
  positionKey: string,
  { cursor, limit, enabled = true }: UsePositionJournalOptions = {},
): UseQueryResult<PositionJournalResult> {
  return useQuery({
    queryKey: positionsQueryKeys.journal(positionKey, cursor, limit),
    queryFn: () => client.getPositionJournal({ positionKey, cursor, limit }),
    enabled,
  });
}

// params가 null이면(표시할 계좌가 아직 없음) 요청하지 않는다 — account_id는 서버 필수
// 파라미터라 빈 값으로 보내면 VALIDATION 오류만 만든다.
export function useNavSeries(
  client: Pick<PositionsClientLike, "getNavSeries">,
  params: NavSeriesParams | null,
): UseQueryResult<NavSeriesResult> {
  return useQuery({
    queryKey: params ? positionsQueryKeys.nav(params) : ["positions", "nav", "disabled"],
    queryFn: () => {
      if (!params) throw new Error("useNavSeries: params가 없으면 호출되지 않아야 합니다.");
      return client.getNavSeries(params);
    },
    enabled: params !== null,
  });
}
