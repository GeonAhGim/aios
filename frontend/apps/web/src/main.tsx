import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { RouterProvider } from "react-router-dom";
import { MfaStepUpDialog } from "./components/MfaStepUpDialog";
import { RateLimitNotice } from "./components/RateLimitNotice";
import "./index.css";
import { router } from "./router";

const queryClient = new QueryClient();

// task-481: 앱 루트 1곳에만 마운트해 configureMfaStepUpHandler로 핸들러를
// 등록한다 — 어느 화면에서든 403 AUTH_MFA_REQUIRED를 받은 요청이 이 하나의
// 다이얼로그를 공유한다. task-841: RateLimitNotice도 같은 이유로 루트에 1곳만
// 마운트한다 — 어느 화면의 useRetryableAction이든 429 RATE_LIMIT_EXCEEDED
// backoff 중이면 이 배너 하나를 공유한다.
createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <QueryClientProvider client={queryClient}>
      <RouterProvider router={router} />
      <MfaStepUpDialog />
      <RateLimitNotice />
    </QueryClientProvider>
  </StrictMode>,
);
