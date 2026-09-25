import { defineConfig } from "vitest/config";

// task-3323: useAuth.test.ts(useLogout)가 @testing-library/react의 renderHook을
// 쓰면서 이 패키지에 처음으로 jsdom이 필요해졌다. 로컬 config가 없으면(다른
// packages/*와 동일한 이유, api-client/vitest.config.ts 참조) 루트 config의
// test.projects가 이 cwd 기준으로는 아무것도 못 찾아 "No projects were found"로
// 죽는다. environment: "jsdom" + execArgv는 apps/web/vitest.config.ts의 동일
// 회귀(Node 22+ 네이티브 experimental webstorage가 jsdom Storage와 충돌해
// useAuthStore.ts의 localStorage 접근이 깨짐)를 이 패키지에서도 막기 위함이다.
export default defineConfig({
  test: {
    environment: "jsdom",
    execArgv: ["--no-experimental-webstorage"],
  },
});
