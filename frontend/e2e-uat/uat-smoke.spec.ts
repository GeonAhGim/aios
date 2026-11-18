import { expect, test, type Page } from "@playwright/test";

import { demoAccount } from "./uatEnv";

async function submitLogin(page: Page, email: string, password: string) {
  await page.goto("/login");
  await page.locator("#email").fill(email);
  await page.locator("#password").fill(password);
  await page.locator('form button[type="submit"]').click();
}

test("wrong password shows a server error, not a network error", async ({ page }) => {
  const { email } = demoAccount();
  await submitLogin(page, email, "definitely-wrong-password-0");

  // The server's INVALID_CREDENTIALS message renders as plain text (no role=alert is exposed).
  await expect(page.getByText("이메일 또는 비밀번호가 올바르지 않습니다.")).toBeVisible();
  // "Failed to fetch" would indicate a CORS/network regression, not a server answer.
  await expect(page.getByText(/failed to fetch|networkerror|load failed/i)).toHaveCount(0);
  await expect(page).toHaveURL(/\/login/);
});

test("demo account login leaves /login and reaches an authenticated screen", async ({ page }) => {
  const { email, password } = demoAccount();
  await submitLogin(page, email, password);

  await expect(page).not.toHaveURL(/\/login/);
  // The demo account lands on the first mandatory onboarding step (MFA setup).
  await expect(page).toHaveURL(/\/onboarding\/mfa-setup/);
  await expect(page.getByRole("heading", { name: "2단계 인증 설정 (필수)" })).toBeVisible();
});

test("demo account visiting /onboarding/demo is guarded to the MFA setup step", async ({ page }) => {
  // Current app behavior: an account with unfinished onboarding is redirected to the first
  // mandatory step, so the offline demo start button is not reachable for the demo account.
  const { email, password } = demoAccount();
  await submitLogin(page, email, password);
  await expect(page).not.toHaveURL(/\/login/);
  await page.goto("/onboarding/demo");
  await expect(page).toHaveURL(/\/onboarding\/mfa-setup/);
  await expect(page.getByRole("heading", { name: "2단계 인증 설정 (필수)" })).toBeVisible();
});
