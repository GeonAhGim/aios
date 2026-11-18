import { expect, test, type Page, type Response } from "@playwright/test";

import { UAT_WEB_URL } from "./uatEnv";
import { createFreshUser, loginAndCompleteOnboarding } from "./uatSession";

// J3 (paper order -> position) against the REAL UAT stack. One throw-away user for the whole file
// (the API allows ~10 auth calls per minute), serial tests, and awaited assertions only: every
// expectation below can fail. Findings about the product are recorded in
// docs/frontend/UNSPECIFIED_ACTIONS.md (UA-5, UA-6, UA-10), not hidden here.
test.describe.configure({ mode: "serial" });
test.setTimeout(240_000);

let page: Page;
const apiStatuses: { method: string; path: string; status: number }[] = [];

test.beforeAll(async ({ browser }) => {
  const context = await browser.newContext({ baseURL: UAT_WEB_URL });
  page = await context.newPage();
  page.on("response", (res: Response) => {
    const url = new URL(res.url());
    if (url.port === "18000" && !url.pathname.startsWith("/auth/")) {
      apiStatuses.push({ method: res.request().method(), path: url.pathname, status: res.status() });
    }
  });
  const user = await createFreshUser();
  await loginAndCompleteOnboarding(page, user);
});

test.afterAll(async () => {
  await page.context().close();
});

test("executions: the creation form and the empty list are shown", async () => {
  await page.goto("/executions");
  await expect(page.getByRole("heading", { name: "실행 제어판" })).toBeVisible();
  for (const label of ["전략 ID", "버전", "배분 자본(USDT)", "거래소", "모드"]) {
    await expect(page.getByLabel(label)).toBeVisible();
  }
  await expect(page.getByLabel("모드")).toHaveValue("PAPER");
  await expect(page.getByText("실행 중인 전략이 없습니다.")).toBeVisible();
});

test("executions: required fields block submission before any request is sent", async () => {
  await page.goto("/executions");
  const posts: string[] = [];
  const listener = (req: { method(): string; url(): string }) => {
    if (req.method() === "POST" && req.url().includes("/executions")) posts.push(req.url());
  };
  page.on("request", listener);
  await page.getByRole("button", { name: "실행 생성" }).click();
  await page.waitForTimeout(800);
  page.off("request", listener);
  expect(posts).toEqual([]);
  const missing = await page
    .getByLabel("전략 ID")
    .evaluate((el) => (el as HTMLInputElement).validity.valueMissing);
  expect(missing).toBe(true);
});

test("executions: a PAPER order without an exchange credential is rejected and the error is visible", async () => {
  await page.goto("/executions");
  await page.getByLabel("전략 ID").fill("uat-paper-strategy");
  const [response] = await Promise.all([
    page.waitForResponse(
      (res) => res.request().method() === "POST" && new URL(res.url()).pathname === "/executions",
    ),
    page.getByRole("button", { name: "실행 생성" }).click(),
  ]);
  // The server refuses: no bitget credential exists for this user (UA-5).
  expect(response.status()).toBe(404);
  const body = await response.text();
  expect(body).toContain("RESOURCE_NOT_FOUND");
  // The screen shows a generic not-found message plus a support code (server reason is not shown, UA-5;
  // the message is not exposed as role=alert, UA-10) -- assert only what the user actually sees.
  await expect(page.getByText("요청한 항목을 찾을 수 없습니다.")).toBeVisible();
  await expect(page.getByText(/지원코드/)).toBeVisible();
  // Nothing was created.
  await expect(page.getByText("실행 중인 전략이 없습니다.")).toBeVisible();
});

test("mandates: without a mandate the screen says every order is blocked", async () => {
  await page.goto("/mandates");
  await expect(page.getByText("위임장 미설정(주문 차단)")).toBeVisible();
  await expect(page.getByText(/모든 주문이 차단된다는 뜻입니다/)).toBeVisible();
});

test("portfolio and dashboard: real empty states for a user with no executions", async () => {
  await page.goto("/portfolio");
  await expect(page.getByRole("heading", { name: "포트폴리오" })).toBeVisible();
  await expect(page.getByText("배분된 실행이 없습니다.")).toBeVisible();
  await expect(page.getByText("포지션 스냅샷이 없습니다.")).toBeVisible();
  await page.goto("/dashboard");
  await expect(page.getByText("현재 실행 중인 운용이 없습니다.")).toBeVisible();
});

test("none of the J3 screens produced a server error (5xx) while loading", async () => {
  expect(apiStatuses.length).toBeGreaterThan(5);
  const serverErrors = apiStatuses.filter((s) => s.status >= 500);
  expect(serverErrors).toEqual([]);
});
