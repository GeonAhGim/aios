import { expect, test, type Page, type Route } from "@playwright/test";
import { mockBackend } from "./support/mockBackend";

// task-10636 (J9): docs/specs/UX_JOURNEYS.md §6.2 J9 4단계("전역 긴급 정지")를 그대로
// 따라간다. 기존 전역 정지 화면(`/admin/safety-controls`)은 AdminRoute로 막혀 §0
// 페르소나(개인 트레이더, is_platform_admin=false — mockBackend 기본 픽스처와 동일)는
// 접근할 수 없다. 대신 `/dashboard`에 신설한 "내 운용 전부 정지" 패널이 useExecutions()가
// 돌려준(= 서버가 이미 호출자 본인 소유로 스코프한) 목록의 RUNNING 건에만
// usePauseExecution을 호출하는지, §6.3 J9 기준(클릭 수 상한 3회, 확인 모달 없이 즉시
// 발동, 같은 화면에 재개 방법 표시)을 충족하는지 검증한다.
//
// sw.js가 "/v1/"로 시작하지 않는 GET(예: /executions)을 캐시 우선으로 처리해 page.route를
// 우회하는 문제(J1/J3 여정 테스트와 동일 증상)를 막기 위해 서비스 워커 등록을 막는다.
test.use({ serviceWorkers: "block" });

const API_BASE = "http://localhost:8000";

function json(route: Route, status: number, body: unknown) {
  return route.fulfill({ status, contentType: "application/json", body: JSON.stringify(body) });
}

const RUNNING_EXECUTION = {
  execution_id: 1,
  strategy_id: "e2e-mine-running",
  strategy_version: "1.0.0",
  status: "RUNNING",
  mode: "PAPER",
  exchange: "bitget",
  allocated_capital: "500",
  days_since_start: 1,
  realized_pnl: "0",
  unrealized_pnl: "0",
  max_drawdown_pct: null,
};

// 다른 사용자 소유라면 애초에 GET /executions 응답에 담기지 않는다(백엔드가 호출자
// 기준으로 스코프). 프론트는 그 응답을 그대로 신뢰하므로, 이 고정 execution_id(99)가
// pause 호출 대상에 전혀 등장하지 않는지로 "타인 자원 미영향"을 검증한다 — 만약 응답에
// 섞여 있었다면(백엔드 결함 재현 시나리오) 현재 프론트 로직은 status==="RUNNING"인
// 모든 건을 정지 대상으로 삼으므로 이 건도 함께 정지됨을 별도로 남겨 둔다.
const OTHER_USER_RUNNING_EXECUTION_ID = 99;

async function mockExecutionActions(page: Page, pauseCalls: number[]) {
  await page.route(`${API_BASE}/executions`, (route) => {
    if (route.request().method() !== "GET") return route.fallback();
    return json(route, 200, [RUNNING_EXECUTION]);
  });
  await page.route(`${API_BASE}/executions/1/pause`, (route) => {
    pauseCalls.push(1);
    return json(route, 200, { ...RUNNING_EXECUTION, status: "PAUSED" });
  });
  await page.route(`${API_BASE}/executions/1/start`, (route) =>
    json(route, 200, { ...RUNNING_EXECUTION, status: "RUNNING" }),
  );
  await page.route(`${API_BASE}/executions/${OTHER_USER_RUNNING_EXECUTION_ID}/pause`, (route) => {
    pauseCalls.push(OTHER_USER_RUNNING_EXECUTION_ID);
    return json(route, 200, {});
  });
}

test.describe("J9 여정: 손실·이상 시 즉시 중단 — 비관리자의 전역 정지 접근", () => {
  test("1단계~4단계 비관리자가 /dashboard 진입 후 1클릭으로 내 운용 전부를 정지하고, 같은 화면에서 재개한다", async ({
    page,
  }) => {
    const pauseCalls: number[] = [];
    await mockBackend(page);
    await mockExecutionActions(page, pauseCalls);
    await page.route(`${API_BASE}/portfolio`, (route) =>
      json(route, 200, {
        allocations: [],
        unallocated_cash: "1000",
        unallocated_cash_weight_pct: "100",
        total_portfolio_value: "1000",
      }),
    );

    // 1단계: 상태 인지 — /dashboard 진입(비관리자 토큰, mockBackend 기본 is_platform_admin=false).
    await page.goto("/dashboard");
    await expect(page.getByRole("heading", { name: "대시보드" })).toBeVisible();
    await expect(page.getByText("e2e-mine-running")).toBeVisible();

    // 2~4단계: 확인 모달 없이 1클릭으로 전역 정지(§6.3 J9 기준 — 클릭 수 상한 3회 이내,
    // 비관리자가 /admin/safety-controls 없이도 전역 정지에 도달).
    await page.getByRole("button", { name: "내 운용 전부 정지" }).click();

    await expect(page.getByText("PAUSED", { exact: true })).toBeVisible();
    expect(pauseCalls).toEqual([1]);
    // 타인 자원 미영향: 존재하지도 않는 타인 소유 execution_id(99)에 대한 pause 호출이
    // 전혀 일어나지 않았다 — useExecutions()가 돌려준 본인 소유 목록 범위 밖은 건드리지 않는다.
    expect(pauseCalls).not.toContain(OTHER_USER_RUNNING_EXECUTION_ID);

    // 정지 후 같은 화면에 재개 방법이 보여야 한다(DoD).
    const resumeButton = page.getByRole("button", { name: "재개" });
    await expect(resumeButton).toBeVisible();
    await resumeButton.click();
    await expect(page.getByRole("button", { name: "재개" })).toHaveCount(0);
  });

  test("[실패 주입] 정지 요청이 거부(403)돼도 화면이 깨지지 않고 권한 오류 안내를 보여준다", async ({ page }) => {
    await mockBackend(page);
    await page.route(`${API_BASE}/executions`, (route) => {
      if (route.request().method() !== "GET") return route.fallback();
      return json(route, 200, [RUNNING_EXECUTION]);
    });
    await page.route(`${API_BASE}/executions/1/pause`, (route) =>
      json(route, 403, {
        error_code: "AUTHZ_FORBIDDEN",
        message: "이 작업을 수행할 권한이 없습니다.",
        details: {},
        trace_id: "e2e-fault-injection-emergency-stop",
        retry_after_seconds: null,
      }),
    );
    await page.route(`${API_BASE}/portfolio`, (route) =>
      json(route, 200, {
        allocations: [],
        unallocated_cash: "1000",
        unallocated_cash_weight_pct: "100",
        total_portfolio_value: "1000",
      }),
    );

    await page.goto("/dashboard");
    await page.getByRole("button", { name: "내 운용 전부 정지" }).click();

    await expect(page.getByText("이 작업을 수행할 권한이 없습니다.")).toBeVisible();
    await expect(page.getByRole("heading", { name: "대시보드" })).toBeVisible();
  });
});
