import { expect, test, type Page } from "@playwright/test";
import { demoAccount } from "./uatEnv";

// 실제 UAT 스택 대상 스모크(mock 없음). 비밀번호는 fill 외 어디에도 쓰지 않는다.

async function loginAsDemo(page: Page) {
  const { email, password } = demoAccount();
  await page.goto("/login");
  await page.locator("#email").fill(email);
  await page.locator("#password").fill(password);
  await page.locator("form button[type=submit]").click();
  await expect(page).not.toHaveURL(/\/login/);
}

test("잘못된 비밀번호 로그인은 네트워크 오류가 아니라 서버 오류 문구를 보여준다", async ({ page }) => {
  // 데모 계정이 아니라 존재하지 않는 계정을 쓴다 -- 실패 누적으로 데모 계정이 잠기는 것을 막는다.
  await page.goto("/login");
  await page.locator("#email").fill(`nobody-${Date.now()}@demo.uat`);
  await page.locator("#password").fill("Wrong-Password-0!x");
  await page.locator("form button[type=submit]").click();

  // Alert (ui-web/Feedback.tsx) carries no role=alert, so match the rendered server error text.
  const alert = page.locator("form").getByText("로그인에 실패했습니다.");
  await expect(alert).toBeVisible();
  // 2026-11-18 CORS 결함 회귀: 오류 본문을 못 읽으면 fetch 단계 TypeError가 노출된다.
  await expect(alert).not.toContainText(/failed to fetch|networkerror|load failed/i);
  await expect(page).toHaveURL(/\/login/);
});

test("데모 계정으로 로그인하면 /login을 벗어나 인증된 화면에 도달한다", async ({ page }) => {
  await loginAsDemo(page);
  await expect(page.locator("#password")).toHaveCount(0);
  await expect(page.locator("main, [role=main]").first()).toBeVisible();
  console.log(`logged-in landing path: ${new URL(page.url()).pathname}`);
});

test("/onboarding/demo는 서버 키 없이 열리고 데모 시작 버튼이 보인다", async ({ page }) => {
  // 라우트가 ProtectedRoute 뒤라 로그인은 필요하지만 거래소 키는 필요 없다.
  await loginAsDemo(page);
  await page.goto("/onboarding/demo");
  await expect(page.getByRole("heading", { name: "데모 모드" })).toBeVisible();
  await expect(page.getByTestId("demo-start-DEMO-BTCUSDT")).toBeVisible();
});
