import { expect, test } from "@playwright/test";

import {
  createFreshUser,
  loginAndCompleteOnboarding,
  readMfaSecret,
  submitLogin,
  submitMfaCode,
  totp,
} from "./uatSession";

test.setTimeout(120_000);

test("J1: fresh account completes MFA setup and risk assessment, then lands on the first screen", async ({
  page,
}) => {
  const user = await createFreshUser();
  await loginAndCompleteOnboarding(page, user);

  await expect(page).not.toHaveURL(/\/(login|onboarding\/(mfa-setup|risk-assessment))/);
  // Both landing screens render a page heading plus the authenticated shell's logout button.
  await expect(page.getByRole("heading").first()).toBeVisible();
  await expect(page.getByRole("button", { name: "로그아웃" })).toBeVisible();
});

test("J1 negative: a wrong TOTP code is rejected with a visible alert", async ({ page }) => {
  const user = await createFreshUser();
  await submitLogin(page, user);
  const secret = await readMfaSecret(page);
  // A code from a far-away time step can never be inside the server's tolerance window.
  await submitMfaCode(page, totp(secret, Date.now() + 3_600_000));

  // The rejection renders as plain text: ui-web's Alert exposes no role=alert (app defect, reported).
  await expect(page.getByText("인증 코드가 올바르지 않습니다.")).toBeVisible();
  await expect(page).toHaveURL(/\/onboarding\/mfa-setup/);
});

test("J1: after MFA setup, logout and re-login demand the 2FA code", async ({ page }) => {
  const user = await createFreshUser();
  const secret = await loginAndCompleteOnboarding(page, user);
  await page.getByRole("button", { name: "로그아웃" }).click();
  await expect(page).toHaveURL(/\/login/);

  // Password alone must not open a session. The server answers with the generic
  // credentials error (it never reveals that MFA is the missing piece).
  await submitLogin(page, user);
  await expect(page).toHaveURL(/\/login/);
  await expect(page.getByText("이메일 또는 비밀번호가 올바르지 않습니다.")).toBeVisible();

  // The server consumes the *current* time step per verification (replay protection), and
  // setup just used one: wait for the next step, then the correct code is accepted.
  const nextStepMs = (Math.floor(Date.now() / 30_000) + 1) * 30_000 + 500;
  await page.waitForTimeout(nextStepMs - Date.now());
  await submitLogin(page, user, totp(secret));
  await expect(page).not.toHaveURL(/\/login/);
});
