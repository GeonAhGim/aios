import tailwindcss from "@tailwindcss/vite";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

// https://vite.dev/config/
export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    port: 5173,
  },
  test: {
    environment: "jsdom",
    passWithNoTests: true,
    coverage: {
      provider: "v8",
      reporter: ["text", "json-summary"],
      reportsDirectory: "coverage",
      // task-2128: 개별 테스트 실패(플레이크)와 커버리지 회귀는 별개 신호다.
      // local_ci의 "test" 스텝이 이미 통과/실패를 게이트하므로, 이 커버리지
      // 스텝은 실패한 실행에서도 리포트를 남겨 래칫이 계속 동작하게 한다.
      reportOnFailure: true,
    },
  },
});
