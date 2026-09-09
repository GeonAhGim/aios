import { expect, test } from "@playwright/test";
import { mockBackend } from "./support/mockBackend";

// H-7b 스모크 2/3 — 차트 지표 오버레이. IndicatorPicker.tsx(GET /v1/indicators,
// envelope=true)를 열어 카탈로그에서 하나를 골라 선택 칩·토글 카운트에
// 반영되는지 확인한다.
test("지표 선택기에서 지표를 고르면 선택 카운트와 칩에 반영된다", async ({ page }) => {
  await mockBackend(page, {
    indicators: [
      { name: "sma", tier: "core", category: "trend" },
      { name: "rsi", tier: "core", category: "momentum" },
    ],
  });
  await page.goto("/chart?instrument_id=BTCUSDT");

  await expect(page.getByTestId("chart-instrument-id")).toHaveText("BTCUSDT");

  const toggleButton = page.getByRole("button", { name: "지표 선택 (0)" });
  await expect(toggleButton).toBeVisible();
  await toggleButton.click();

  const option = page.locator("#indicator-option-sma");
  await expect(option).toBeVisible();
  await option.click();

  await expect(page.getByRole("button", { name: "지표 선택 (1)" })).toBeVisible();
  await expect(page.getByRole("list", { name: "선택된 지표" }).getByText("sma ✕")).toBeVisible();
});
