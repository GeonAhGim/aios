import { createLogoutClient, type LogoutClient } from "@aios/api-client";
import type { LoginRequest, SignupRequest } from "@aios/shared-types";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useMemo } from "react";
import { apiClient } from "./clientInstance";
import { useAuthStore } from "./useAuthStore";

const baseUrl = import.meta.env.VITE_API_BASE_URL ?? "http://localhost:8000";

export function useMe() {
  const token = useAuthStore((s) => s.token);
  return useQuery({
    queryKey: ["me"],
    queryFn: () => apiClient.getMe(),
    enabled: !!token,
    retry: false,
  });
}

export function useSignup() {
  const setToken = useAuthStore((s) => s.setToken);
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: SignupRequest) => apiClient.register(body),
    onSuccess: (data) => {
      setToken(data.accessToken);
      qc.invalidateQueries({ queryKey: ["me"] });
    },
  });
}

export function useLogin() {
  const setToken = useAuthStore((s) => s.setToken);
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: LoginRequest) => apiClient.login(body),
    onSuccess: (data) => {
      setToken(data.accessToken);
      qc.invalidateQueries({ queryKey: ["me"] });
    },
  });
}

// spec §3.4 + §9 PLT-24. 감사 지적("로그아웃 no-op"): 이 훅은 원래 서버를
// 전혀 호출하지 않고 로컬 상태만 비웠다 — task-3323이 api-client/logout.ts의
// createLogoutClient(서버 베스트 에포트 POST /auth/logout + 로컬 정리)로
// 교체해 실제 로그아웃 요청이 나가도록 고쳤다. 반환값이 Promise로 바뀌었으니
// 호출부는 await해야 정리가 끝난 뒤 다음 동작(navigate 등)을 할 수 있다.
export function useLogout() {
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
  return async () => {
    await client.logout();
    qc.clear();
  };
}

export function useSetupMfa() {
  return useMutation({ mutationFn: () => apiClient.setupMfa() });
}

export function useVerifyMfa() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (totpCode: string) => apiClient.verifyMfa({ totpCode }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["me"] }),
  });
}
