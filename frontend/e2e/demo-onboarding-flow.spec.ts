import { expect, test } from "@playwright/test";
import { mockBackend } from "./support/mockBackend";

// task-2632(U-10) DoD: "Playwright 흐름 1건(데모→차트→백테스트)". 데모 모드는
// 고정 샘플 데이터셋(demoDataset.ts)만으로 동작해 실 시세/실행 백엔드를 타지
// 않는다 — mockBackend는 ProtectedRoute(useMe/useRiskProfile)를 통과시키는
// 최소 인증 계약만 채워준다(H-7b 스모크와 동일 관용).
test("데모 모드에서 종목을 골라 차트를 본 뒤 백테스트를 실행하면 요약이 표시된다", async ({ page }) => {
  await mockBackend(page);
  await page.goto("/onboarding/demo");

  await expect(page.getByRole("heading", { name: "데모 모드" })).toBeVisible();

  await page.getByTestId("demo-start-DEMO-BTCUSDT").click();

  await expect(page.getByTestId("demo-chart-canvas")).toBeVisible();

  const runButton = page.getByTestId("demo-backtest-run");
  await expect(runButton).toBeEnabled();
  await runButton.click();

  await expect(page.getByTestId("demo-backtest-summary")).toBeVisible();
  await expect(page.getByText("최종 자산")).toBeVisible();
});
