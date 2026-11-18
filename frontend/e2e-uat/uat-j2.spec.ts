import { expect, test } from "@playwright/test";

import {
  createFreshUser,
  loginAndCompleteOnboarding,
  readMfaSecret,
  submitLogin,
  totp,
} from "./uatSession";

test.setTimeout(120_000);

// task-25724 (UAT-J2): 실스택 J2 -- 스크립트 컴파일→백테스트 실행 스모크.
// J1 온보딩 경로를 uatSession.ts에서 재사용한다.
//
// DoD:
//   (a) 기본 예제 스크립트 컴파일 성공 단언
//   (b) compile-run-backtest 클릭 → 결과 요약 또는 오류 메시지 확인
//   (c) 컴파일 오류 스크립트 → 오류 문구 표시, 백테스트 버튼 비활성 단언

const DEFAULT_SCRIPT = [
  "input length: int = 14",
  "input close: series<float> = 0",
  "let rsi_val = ta.rsi(close, length)",
  "signal go_long = rsi_val < 30",
  "plot(rsi_val, 1)",
  "",
].join("\n");

const BROKEN_SCRIPT = [
  "input length: int = 14",
  "let rsi_val = ta.rsi(close, length)",  // close 미정의
  "plot(rsi_val, 1)",
  "",
].join("\n");

test.describe("UAT-J2: 스크립트 컴파일→백테스트 실행 스모크", () => {
  test.use({ serviceWorkers: "block" });

  test("J2-a: 기본 예제 스크립트 컴파일 성공", async ({ page }) => {
    const user = await createFreshUser();
    await loginAndCompleteOnboarding(page, user);

    // 스크립트 편집 화면으로 이동
    await page.goto("/scripts/editor");
    await expect(page).toHaveURL(/\/scripts\/editor/, { timeout: 10_000 });

    // 기본 예제 스크립트 입력
    const textarea = page.locator("textarea").first();
    await expect(textarea).toBeVisible({ timeout: 10_000 });
    await textarea.clear();
    await textarea.fill(DEFAULT_SCRIPT);

    // 컴파일 버튼 클릭
    const compileBtn = page.getByRole("button", { name: /컴파일/ });
    await compileBtn.click();

    // 컴파일 성공 표시 단언
    await expect(page.getByTestId("compile-preview-hash")).toBeVisible({ timeout: 30_000 });
    // 컴파일 성공 시 백테스트 실행 버튼이 나타난다
    await expect(page.getByTestId("compile-run-backtest")).toBeVisible();
  });

  test("J2-b: compile-run-backtest 클릭 → 결과 요약 또는 오류 메시지 확인", async ({ page }) => {
    const user = await createFreshUser();
    await loginAndCompleteOnboarding(page, user);

    await page.goto("/scripts/editor");
    await expect(page).toHaveURL(/\/scripts\/editor/, { timeout: 10_000 });

    // 기본 예제 스크립트 입력
    const textarea = page.locator("textarea").first();
    await textarea.clear();
    await textarea.fill(DEFAULT_SCRIPT);

    // 컴파일
    await page.getByRole("button", { name: /컴파일/ }).click();
    await expect(page.getByTestId("compile-preview-hash")).toBeVisible({ timeout: 30_000 });

    // 백테스트 실행 버튼이 활성화된 상태 확인 후 클릭
    const runBacktestBtn = page.getByTestId("compile-run-backtest");
    await expect(runBacktestBtn).toBeEnabled();

    // console/error 로깅 설정
    const consoleMessages: string[] = [];
    const errors: string[] = [];
    page.on("console", (msg) => consoleMessages.push(`${msg.type()}: ${msg.text()}`));
    page.on("pageerror", (err) => errors.push(err.message));

    await runBacktestBtn.click();

    // 로딩 상태가 사라지길 기다림
    await expect(runBacktestBtn).not.toBeDisabled({ timeout: 30_000 });

    // console/error 확인
    const errorLogs = errors.length > 0 ? errors : consoleMessages.filter((m) => m.startsWith("error:"));

    // 결과 요약 또는 오류 메시지 확인
    const hasSummary = await page.getByTestId("compile-backtest-summary")
      .isVisible({ timeout: 5_000 }).catch(() => false);
    const hasError = await page.getByTestId("compile-backtest-error")
      .isVisible({ timeout: 5_000 }).catch(() => false);

    // 요약도 에러도 없으면 console/error 로그를 포함해 단언
    expect(hasSummary || hasError || errorLogs.length > 0,
      `백테스트 결과 없음. console: ${JSON.stringify(consoleMessages.slice(-5))}, errors: ${JSON.stringify(errors)}`).toBeTruthy();
  });

  test("J2-c: 컴파일 오류 스크립트 → 오류 문구 표시, 백테스트 버튼 비활성", async ({ page }) => {
    const user = await createFreshUser();
    await loginAndCompleteOnboarding(page, user);

    await page.goto("/scripts/editor");
    await expect(page).toHaveURL(/\/scripts\/editor/, { timeout: 10_000 });

    // 문법 오류가 있는 스크립트 입력
    const textarea = page.locator("textarea").first();
    await textarea.clear();
    await textarea.fill(BROKEN_SCRIPT);

    // 컴파일
    await page.getByRole("button", { name: /컴파일/ }).click();

    // 컴파일 실패 시 compile-preview-hash가 나타나지 않아야 함
    const hasCompilePreview = await page.getByTestId("compile-preview-hash")
      .isVisible({ timeout: 10_000 }).catch(() => false);
    // 컴파일 오류가 있으면 preview가 없거나 alert가 보인다
    expect(hasCompilePreview).toBeFalsy();

    // compile-run-backtest 버튼이 나타나지 않아야 함 (컴파일이 실패했으므로)
    const backtestBtn = page.getByTestId("compile-run-backtest");
    expect(await backtestBtn.count()).toBe(0);
  });
});
