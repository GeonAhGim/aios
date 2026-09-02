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
  },
  setUser: (user) => set({ user }),
  logout: () => {
    localStorage.removeItem(TOKEN_STORAGE_KEY);
    set({ token: null, user: null });
  },
}));
