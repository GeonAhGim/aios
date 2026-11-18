import { expect, test, type Page } from "@playwright/test";
import { fieldControl } from "../e2e/support/fieldControl";
import { loadUatDemoAccount, MASKED } from "./uatEnv";

// task-25677: 실제 UAT 스택 스모크(mock 없음). 주문·백테스트 등 J2/J3 단계는 범위 밖이다.
// 계정 비밀번호는 어떤 assertion 메시지에도 넣지 않는다(실패 출력에 노출되지 않도록 MASKED 사용).
const account = loadUatDemoAccount();

// /onboarding/demo 등은 protect()로 감싸여 있어 비인증이면 /login으로 돌아간다 -- 로그인 후 진입한다.
async function loginWithDemoAccount(page: Page) {
  await page.goto("/login");
  await fieldControl(page, "이메일").fill(account.email);
  const passwordField = fieldControl(page, "비밀번호");
  await passwordField.fill(account.password);
  await page.getByRole("button", { name: "로그인" }).click();
  // 실패 시 Playwright error-context 스냅샷에 입력값이 남지 않도록 제출 직후 비운다.
  await passwordField.fill("");
}

test.describe("UAT 스모크 (실제 스택)", () => {
  test("(a) 잘못된 비밀번호 로그인은 서버 오류 문구를 보여준다 (네트워크 오류 아님)", async ({ page }) => {
    await page.goto("/login");
    await expect(page.getByRole("heading", { name: "로그인" }).first()).toBeVisible();

    await fieldControl(page, "이메일").fill(account.email);
    await fieldControl(page, "비밀번호").fill(`${MASKED}-wrong-password`);
    await page.getByRole("button", { name: "로그인" }).click();

    // 오류 문구는 ErrorMessage 영역에 렌더링된다(role=alert 없음 -- 텍스트로 단언).
    const serverMessage = page.getByText("이메일 또는 비밀번호가 올바르지 않습니다");
    await expect(serverMessage).toBeVisible();
    // 2026-11-18 CORS 결함 회귀: 네트워크 실패면 앱이 일반 문구("로그인에 실패했습니다.")만 보여준다.
    await expect(page.getByText("로그인에 실패했습니다.")).toHaveCount(0);
    await expect(page).toHaveURL(/\/login/);
  });

  test("(b) 데모 계정으로 로그인하면 /login을 벗어나 인증된 화면에 도달한다", async ({ page }) => {
    await loginWithDemoAccount(page);

    await expect(page).not.toHaveURL(/\/login/);
    await expect(page).toHaveURL(/\/dashboard/);
    await expect(page.getByRole("heading", { name: "대시보드" })).toBeVisible();
  });

  test("(c) /onboarding/demo가 열리고 데모 시작 버튼이 보인다 (서버 키 불필요)", async ({ page }) => {
    await loginWithDemoAccount(page);
    await page.goto("/onboarding/demo");
    await expect(page.getByRole("heading", { name: "데모 모드" })).toBeVisible();
    await expect(page.locator('[data-testid^="demo-start-"]').first()).toBeVisible();
  });
});
