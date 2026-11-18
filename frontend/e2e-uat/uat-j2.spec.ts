import { expect, test, type Page, type Response } from "@playwright/test";

import { UAT_WEB_URL } from "./uatEnv";
import { createFreshUser, loginAndCompleteOnboarding } from "./uatSession";

// J2 (script -> backtest) against the REAL UAT stack: one throw-away user, serial tests, awaited
// assertions only. Product findings live in docs/frontend/UNSPECIFIED_ACTIONS.md (UA-10, UA-11, UA-12).
test.describe.configure({ mode: "serial" });
test.setTimeout(240_000);

const GOOD_SCRIPT = [
  "input length: int = 14",
  "input close: series<float> = 0",
  "let rsi_val = ta.rsi(close, length)",
  "signal go_long = rsi_val < 30",
  "plot(rsi_val, 1)",
  "",
].join("\n");

// `close` is used without being declared.
const BROKEN_SCRIPT = [
  "input length: int = 14",
  "let rsi_val = ta.rsi(close, length)",
  "plot(rsi_val, 1)",
  "",
].join("\n");

let page: Page;
const api: { method: string; path: string; status: number; body: string }[] = [];

test.beforeAll(async ({ browser }) => {
  const context = await browser.newContext({ baseURL: UAT_WEB_URL });
  page = await context.newPage();
  page.on("response", async (res: Response) => {
    const url = new URL(res.url());
    if (url.port !== "18000" || url.pathname.startsWith("/auth/")) return;
    let body = "";
    if (res.status() >= 400) body = await res.text().catch(() => "");
    api.push({ method: res.request().method(), path: url.pathname, status: res.status(), body });
  });
  await loginAndCompleteOnboarding(page, await createFreshUser());
});

test.afterAll(async () => {
  await page.context().close();
});

async function compile(script: string): Promise<void> {
  await page.goto("/scripts/editor");
  const editor = page.locator("textarea").first();
  await expect(editor).toBeVisible();
  await editor.clear();
  await editor.fill(script);
  await page.getByRole("button", { name: /컴파일/ }).click();
}

test("script editor: a valid script compiles against the real compiler", async () => {
  await compile(GOOD_SCRIPT);
  await expect(page.getByTestId("compile-preview-hash")).toHaveText(/[0-9a-f]{64}/, { timeout: 30_000 });
  await expect(page.getByText("명령 수")).toBeVisible();
  await expect(page.getByTestId("compile-run-backtest")).toBeVisible();
  const post = api.find((c) => c.method === "POST" && c.path === "/v1/scripts/compile");
  expect(post?.status).toBe(200);
});

test("script editor: running a backtest without an instrument explains what is missing", async () => {
  await compile(GOOD_SCRIPT);
  await expect(page.getByTestId("compile-run-backtest")).toBeVisible({ timeout: 30_000 });
  const before = api.length;
  await page.getByTestId("compile-run-backtest").click();
  await expect(
    page.getByText("백테스트를 실행하려면 instrument_id가 지정된 주소로 여세요."),
  ).toBeVisible();
  const backtestCalls = api.slice(before).filter((c) => c.path.includes("backtest"));
  expect(backtestCalls).toEqual([]);
});

test("script editor: an undeclared identifier is rejected with its position, no run button", async () => {
  await compile(BROKEN_SCRIPT);
  await expect(page.getByText(/2행 1열/)).toBeVisible({ timeout: 30_000 });
  await expect(page.getByText("SCRIPT_TYPE")).toBeVisible();
  await expect(page.getByTestId("compile-preview-hash")).toHaveCount(0);
  await expect(page.getByTestId("compile-run-backtest")).toHaveCount(0);
  const rejected = [...api].reverse().find((c) => c.path === "/v1/scripts/compile");
  expect(rejected?.status).toBe(400);
  expect(rejected?.body).toContain("VALIDATION_INVALID_FIELD");
});

test("market screens load without server errors (symbols may be empty in UAT)", async () => {
  await page.goto("/market/instruments");
  await expect(
    page.getByText("표시할 심볼이 없습니다.").or(page.getByRole("row").nth(1)),
  ).toBeVisible();
  await page.goto("/screener");
  await expect(page.getByText("필터를 구성하고 실행하세요.")).toBeVisible();
  await page.goto("/chart");
  await expect(page.getByText("심볼을 먼저 선택하세요.").or(page.locator("canvas").first())).toBeVisible();
  expect(api.filter((c) => c.status >= 500)).toEqual([]);
});
