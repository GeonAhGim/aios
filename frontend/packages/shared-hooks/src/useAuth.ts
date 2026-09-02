import type { LoginRequest, SignupRequest } from "@aios/shared-types";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { apiClient } from "./clientInstance";
import { useAuthStore } from "./useAuthStore";

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

export function useLogout() {
  const logout = useAuthStore((s) => s.logout);
  const qc = useQueryClient();
  return () => {
    logout();
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
