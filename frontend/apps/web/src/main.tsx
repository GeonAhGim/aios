import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { RouterProvider } from "react-router-dom";
import { InstallPrompt } from "./components/InstallPrompt";
import { MfaStepUpDialog } from "./components/MfaStepUpDialog";
import { OfflineBanner } from "./components/OfflineBanner";
import { RateLimitNotice } from "./components/RateLimitNotice";
import "./index.css";
import { registerServiceWorker } from "./lib/serviceWorker";
import { router } from "./router";

const queryClient = new QueryClient();

// task-481: 앱 루트 1곳에만 마운트해 configureMfaStepUpHandler로 핸들러를
// 등록한다 — 어느 화면에서든 403 AUTH_MFA_REQUIRED를 받은 요청이 이 하나의
// 다이얼로그를 공유한다. task-841: RateLimitNotice도 같은 이유로 루트에 1곳만
// 마운트한다 — 어느 화면의 useRetryableAction이든 429 RATE_LIMIT_EXCEEDED
// backoff 중이면 이 배너 하나를 공유한다. task-2700(UX-16): OfflineBanner·
// InstallPrompt도 동일한 "루트 1곳" 관용을 따른다.
createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <QueryClientProvider client={queryClient}>
      <RouterProvider router={router} />
      <MfaStepUpDialog />
      <RateLimitNotice />
      <OfflineBanner />
      <InstallPrompt />
    </QueryClientProvider>
  </StrictMode>,
);

// UX-16: 오프라인 셸을 위한 서비스워커 등록. StrictMode의 2회 mount에도
// registerServiceWorker가 내부에서 memoize하므로 실제 등록은 1회만 나간다.
void registerServiceWorker();
