import { expect, test } from "@playwright/test";
import { mockBackend } from "./support/mockBackend";

// H-7b 스모크 3/3 — 백테스트 실행 결과 표시. BacktestPanel.tsx(POST
// /v1/backtests/quick, envelope=true)를 "실행"까지 눌러 결과 요약(dl
// data-testid="backtest-summary")이 렌더되는지 확인한다. 캔들이 있어야
// runDisabled가 풀리므로 mockBackend의 candles 픽스처(24봉)에 의존한다.
test("차트에서 즉시 백테스트를 실행하면 결과 요약이 표시된다", async ({ page }) => {
  await mockBackend(page);
  await page.goto("/chart?instrument_id=BTCUSDT");

  await expect(page.getByTestId("chart-instrument-id")).toHaveText("BTCUSDT");

  const runButton = page.getByTestId("backtest-run");
  await expect(runButton).toBeEnabled();
  await runButton.click();

  const summary = page.getByTestId("backtest-summary");
  await expect(summary).toBeVisible();
  await expect(summary).toContainText("10500");
});
