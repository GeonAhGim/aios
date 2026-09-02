// spec §3.4 + §9 PLT-24. api-client/logout.ts(createLogoutClient)가
// 서버 베스트 에포트 호출 + 로컬 정리 + 진행 중 refresh 취소를 맡고,
// 이 훅은 그 위에 앱 계층 상태(useAuthStore 토큰, TanStack Query 캐시)를
// 함께 비우는 배선만 담당한다.
//
// task-427/413~415와의 충돌을 피하기 위해 http.ts·AiosApiClient에는
// 아직 연결하지 않는다(이 leaf의 decision) — AppShell 등 실제 사용처
// 교체는 후속 리프에서 한 번에 한다.
import { createLogoutClient, type LogoutClient } from "@aios/api-client";
import { useAuthStore } from "@aios/shared-hooks";
import { useQueryClient } from "@tanstack/react-query";
import { useMemo } from "react";

const baseUrl = import.meta.env.VITE_API_BASE_URL ?? "http://localhost:8000";

export interface UseLogoutResult {
  logout(): Promise<void>;
  logoutAll(): Promise<void>;
}

export function useLogout(): UseLogoutResult {
  const qc = useQueryClient();
  const client: LogoutClient = useMemo(
    () =>
      createLogoutClient({
        baseUrl,
        getToken: () => useAuthStore.getState().token,
        store: { clear: () => useAuthStore.getState().logout() },
      }),
    [],
  );

  return useMemo(
    () => ({
      logout: async () => {
        await client.logout();
        qc.clear();
      },
      logoutAll: async () => {
        await client.logoutAll();
        qc.clear();
      },
    }),
    [client, qc],
  );
}
