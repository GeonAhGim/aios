// spec §3.4 + §9 PLT-24. api-client/logout.ts(createLogoutClient)가
// 서버 베스트 에포트 호출 + 로컬 정리 + 진행 중 refresh 취소를 맡고,
// 이 훅은 그 위에 앱 계층 상태(useAuthStore 토큰, TanStack Query 캐시)를
// 함께 비우는 배선만 담당한다.
//
// task-427/413~415와의 충돌을 피하기 위해 http.ts·AiosApiClient에는
// 아직 연결하지 않는다(이 leaf의 decision).
//
// task-3323: AppShell 등 실사용처의 "로그아웃 no-op" 감사 지적은 이 훅을
// AppShell에 새로 배선하는 대신 @aios/shared-hooks(useAuth.ts)의 기존
// useLogout 구현 자체를 이 파일과 같은 createLogoutClient 배선으로
// 교체해 해소했다 — AppShell을 비롯한 수십 개 페이지 테스트가 이미
// "@aios/shared-hooks" 모듈 전체를 목으로 대체하는 경계에 맞춰져 있어서,
// 그 경계 안에서 실제 서버 호출을 추가하는 쪽이 새 QueryClientProvider
// 배선을 모든 호출부 테스트에 추가하는 것보다 훨씬 작은 변경이었다.
// 이 훅은 여전히 유효한 독립 진입점(예: logoutAll 등 다른 실사용처가
// 필요해지면 재사용)으로 남겨둔다.
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
