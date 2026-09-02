import { configureUnauthorizedHandler, resetUnauthorizedGuard } from "@aios/api-client";
import type { UserResponse } from "@aios/shared-types";
import { create } from "zustand";

const TOKEN_STORAGE_KEY = "aios_access_token";

interface AuthState {
  token: string | null;
  user: UserResponse | null;
  setToken: (token: string) => void;
  setUser: (user: UserResponse | null) => void;
  logout: () => void;
}

// 클라이언트 로컬 상태(토큰, 현재 사용자)만 Zustand로 관리한다 — 서버 데이터는
// 여기 두지 않는다(TanStack Query와 역할 분리, 17번 문서 §17.4 원칙).
export const useAuthStore = create<AuthState>((set) => ({
  token: typeof window !== "undefined" ? localStorage.getItem(TOKEN_STORAGE_KEY) : null,
  user: null,
  setToken: (token) => {
    localStorage.setItem(TOKEN_STORAGE_KEY, token);
    set({ token });
    // task-354: 새 세션이 시작됐으니 다음 401도 다시 알림 대상이 되어야 한다.
    resetUnauthorizedGuard();
  },
  setUser: (user) => set({ user }),
  logout: () => {
    localStorage.removeItem(TOKEN_STORAGE_KEY);
    set({ token: null, user: null });
  },
}));

// task-354: api-client의 401 AUTH_* 훅을 이 모듈이 앱 부트스트랩 시점에
// 1회 구독한다 — api-client는 스토어를 모르고, 스토어만 api-client를 안다
// (단방향 의존 유지). 실제 화면 리다이렉트는 ProtectedRoute가 token 변화에
// 반응해서 처리한다(원경로 next 보존 포함).
configureUnauthorizedHandler(() => useAuthStore.getState().logout());
